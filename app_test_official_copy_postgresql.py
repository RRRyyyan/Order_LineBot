# -*- coding: utf-8 -*-

# ==============================================================================
#  檔案說明
# ==============================================================================
#
#  主應用程式檔案 (app_test_official_copy_postgresql.py)
#  - 使用 Flask 框架建立 Web 伺服器
#  - 整合 LINE Messaging API，處理來自 LINE 的 Webhook 事件
#  - 實現訂餐機器人的核心功能：開團、點餐、閉團、查詢訂單等
#  - 使用 SQLAlchemy 與 PostgreSQL 資料庫儲存團購與訂單資訊
#  - 使用 Redis 快取部分資料 (如活躍團購、使用者選擇的團購)
#  - 使用 APScheduler 定時檢查並關閉過期的團購
#
# ==============================================================================
#  導入所需函式庫
# ==============================================================================
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
# 主要 API 和請求/訊息類型從 messaging 導入
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,  # 新增：用於處理 Rich Menu 圖片
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,  # 新增：用於發送圖片
    FlexMessage,
    FlexBubble,
    FlexBox,
    FlexText,
    FlexIcon,
    FlexButton,
    FlexSeparator,
    FlexContainer,
    # Rich Menu 相關
    RichMenuRequest,  # 新增
    RichMenuSize,     # 新增
    RichMenuBounds,   # 新增
    RichMenuArea,     # 新增
    # Template 相關
    TemplateMessage,  # 新增
    CarouselTemplate, # 新增
    CarouselColumn,   # 新增
    # Action 相關
    MessageAction,    # 新增
    PostbackAction,   # 新增
    DatetimePickerAction, # 新增
    # Quick Reply 相關
    QuickReply,      # 新增
    QuickReplyItem,   # 新增
    PushMessageRequest
)

from linebot.v3.webhooks import (
    MessageEvent, FollowEvent, PostbackEvent, TextMessageContent,
)
from collections import Counter
import re
import os
import time
import json
from redis import Redis
from datetime import datetime, timedelta, UTC, timezone
from apscheduler.schedulers.background import BackgroundScheduler

# 導入本地模組
from config import get_config, Config, OrderConfig, LineBotConfig
from database import app, db, DatabaseManager, GroupOrder, UserOrder

# ==============================================================================
#  應用程式配置與初始化
# ==============================================================================

# 獲取環境配置 (預設為 development)
env_config = get_config('development' if app.debug else 'production')

# 初始化 Redis 客戶端
redis_client = Redis.from_url(env_config.REDIS_URL, decode_responses=True)

# 初始化資料庫管理器
db_manager = DatabaseManager(redis_client)

# LINE Bot SDK 配置
configuration = Configuration(access_token=env_config.CHANNEL_ACCESS_TOKEN)
line_handler = WebhookHandler(env_config.CHANNEL_SECRET)

# 使用者名稱快取
user_names_cache = {}

# ==============================================================================
#  輔助函式
# ==============================================================================

def get_user_name(user_id: str) -> str:
    """
    獲取 LINE 使用者的顯示名稱。
    優先從記憶體快取讀取，若快取未命中則呼叫 LINE API 獲取。
    """
    if user_id in user_names_cache:
        return user_names_cache[user_id]
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
            user_names_cache[user_id] = user_name
            return user_name
    except Exception as e:
        app.logger.error(f"無法獲取用戶 {user_id} 的資料: {e}")
        return f"用戶 {user_id[:5]}..."

@app.route("/callback", methods=["POST"])
def callback():
    """
    接收 LINE Platform 送來的 Webhook 請求。
    驗證簽名，並將請求交由 WebhookHandler 處理。
    """
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Check your channel access token/secret.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error handling webhook: {e}")
        abort(500)
    return "OK"

@line_handler.add(FollowEvent)
def handle_follow(event):
    """
    處理使用者加入好友或解除封鎖的事件。
    目前僅記錄事件類型。
    """
    user_id = event.source.user_id
    welcome_message = "歡迎加入Twinkle團購機器人，您可以透過下方功能列進行相關操作或查看說明"
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=welcome_message)]
        )
    )
    app.logger.info(f"用戶 {user_id} 觸發了 {event.type} 事件")

@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    處理收到的文字訊息。
    根據訊息內容，執行不同的訂餐機器人功能。
    """
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id
        text = event.message.text.strip()

        # 檢查使用者是否正在等待輸入備註
        state_key = f'user_state:{user_id}'
        user_state = redis_client.hgetall(state_key)
        
        if user_state and user_state.get('state') == 'waiting_note_input':
            group_order_id = user_state.get('group_order_id')
            item = user_state.get('item')
            new_note = text
            
            # 清除狀態
            redis_client.delete(state_key)
            
            if new_note.lower() == '取消':
                reply_text = "已取消修改備註。"
                # 可以選擇重新顯示修改介面
                handle_edit_order(event, line_bot_api, group_order_id, user_id)
                # line_bot_api.reply_message(
                #     ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
                # )
            else:
                # 調用更新備註的函數 (注意：我們需要修改 handle_update_note)
                handle_update_note(event, line_bot_api, group_order_id, item, new_note)
                # handle_update_note 會處理回覆和重新顯示介面，所以這裡不用再回覆
            return # 處理完畢，結束此函數

        # --- 功能分支判斷 ---
        if text == "開團":
            handle_start_group_selection(event, line_bot_api)
        elif text == "我的開團":
            handle_show_user_closed_groups(event, line_bot_api, user_id)
        elif text.endswith("開團"):
            handle_create_group_intent(event, line_bot_api, text, user_id)
        elif text == "閉團":
            handle_close_group_selection(event, line_bot_api, user_id)
        elif text.endswith("閉團") and text != "閉團":
            handle_close_group_action(event, line_bot_api, text, user_id)
        elif text == "目前團購":
            handle_show_active_groups(event, line_bot_api)
        elif text.startswith("我要點"):
            handle_add_order_item(event, line_bot_api, text, user_id)
        elif text == "我的訂單":
            handle_user_order_summary(event, line_bot_api)
        elif redis_client.exists(f'waiting_time_input:{user_id}'):
            handle_custom_close_time_input(event, line_bot_api, text, user_id)

@line_handler.add(PostbackEvent)
def handle_postback(event):
    """
    處理使用者點擊 Template Message 中的按鈕 (PostbackAction) 所觸發的事件。
    根據 postback data 執行相應操作。
    """
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        data = event.postback.data
        user_id = event.source.user_id
        params = event.postback.params if hasattr(event.postback, 'params') else {}
        
        # 解析 postback 數據
        if "action=" in data:
            action = data.split("action=")[1].split("&")[0]
            params = dict(param.split("=") for param in data.split("&")[1:])
            
            if action == "edit_order":
                handle_edit_order(event, line_bot_api, params.get("group_order_id"), user_id)
            elif action == "increase_item":
                handle_increase_item(event, line_bot_api, params.get("group_order_id"), params.get("item"))
            elif action == "decrease_item":
                handle_decrease_item(event, line_bot_api, params.get("group_order_id"), params.get("item"))
            elif action == "prompt_update_note":
                group_order_id = params.get("group_order_id")
                item = params.get("item")
                if group_order_id and item:
                    # 將使用者狀態存入 Redis，表示正在等待輸入備註
                    state_key = f'user_state:{user_id}'
                    redis_client.hset(state_key, mapping={
                        'state': 'waiting_note_input',
                        'group_order_id': group_order_id,
                        'item': item
                    })
                    # 設置一個超時時間，例如 5 分鐘
                    redis_client.expire(state_key, 300)

                    # 解析原始商品名稱
                    item_name = item
                    if "(" in item and ")" in item:
                        item_name = item[:item.find("(")]

                    reply_text = f"請輸入【{item_name}】的新備註：(輸入\"取消\"可放棄)"
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                    )
                else:
                    app.logger.error("處理 prompt_update_note 時缺少參數")
            elif action == "update_note":
                # 這個 action 實際上已不再被 Flex Message 使用，但保留以防萬一
                # 如果需要處理來自舊介面或其他來源的此 action，可以在這裡加入邏輯
                app.logger.warning(f"收到已棄用的 update_note action: {params}")
                pass # 或添加處理邏輯
            elif action == "save_edit":
                handle_save_edit(event, line_bot_api, params.get("group_order_id"))
            elif action == "clear_my_order":
                handle_delete_order_action(event, line_bot_api, params.get("group_order_id"), user_id)
        else:
            app.logger.info(f"收到 Postback: data={data}, params={params}, user_id={user_id}")

            # --- Postback 功能分支判斷 ---
            ## 結束此團購
            if data.startswith("close_group_"):
                restaurant = data.replace("close_group_", "")
                handle_close_group_action(event, line_bot_api, restaurant, user_id)
            ## 選擇此團購
            elif data.startswith("select_group_"):
                group_order_id = data.replace("select_group_", "")
                handle_select_group_action(event, line_bot_api, group_order_id, user_id)
            ## 編輯訂單
            elif data.startswith("edit_order_"):
                group_order_id = data.replace("edit_order_", "")
                handle_edit_order(event, line_bot_api, group_order_id, user_id)
            ## 查看菜單
            elif data.startswith("menu_"):
                restaurant = data.replace("menu_", "")
                handle_show_menu_action(event, line_bot_api, restaurant)
            ## 刪除訂單 
            elif data.startswith("delete_order_"):
                group_order_id = data.replace("delete_order_", "")
                handle_delete_order_action(event, line_bot_api, group_order_id, user_id)
            ## 設定閉團時間
            elif data.startswith("set_time_"):
                group_order_id = data.replace("set_time_", "")
                handle_set_close_time_action(event, line_bot_api, group_order_id, user_id, params)

def create_rich_menu():
    """創建 LINE Bot 的 Rich Menu"""
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_blob_api = MessagingApiBlob(api_client)

            rich_menu_request = RichMenuRequest(
                size=RichMenuSize(**env_config.RICH_MENU_SIZE),
                selected=True,
                name="圖文選單 1",
                chat_bar_text="查看更多資訊",
                areas=[
                    RichMenuArea(
                        bounds=RichMenuBounds(**area['bounds']),
                        action=MessageAction(text=area['action'])
                    ) for area in LineBotConfig.RICH_MENU_AREAS
                ]
            )

            rich_menu_id = line_bot_api.create_rich_menu(rich_menu_request=rich_menu_request).rich_menu_id
            print(f"Rich menu created: {rich_menu_id}")

            with open(f"{env_config.STATIC_FOLDER}/{env_config.RICH_MENU_IMAGE}", 'rb') as image:
                line_bot_blob_api.set_rich_menu_image(
                    rich_menu_id=rich_menu_id,
                    body=bytearray(image.read()),
                    _headers={'Content-Type': 'image/png'}
                )
            print("Rich menu image uploaded")

            line_bot_api.set_default_rich_menu(rich_menu_id=rich_menu_id)
            print("Rich menu set as default")
            return rich_menu_id
    except Exception as e:
        print(f"Error creating rich menu: {e}")
        return None

def initialize_redis_and_db():
    try:
        # 確保資料庫表格存在
        with app.app_context():
            db.create_all()
            
        # 清除所有 Redis 資料
        all_keys = redis_client.keys('*')
        
        # 如果有找到鍵值才執行刪除
        if all_keys:
            redis_client.delete(*all_keys)
        
        # 從 PostgreSQL 讀取活躍訂單
        with app.app_context():
            active_orders = GroupOrder.query.filter_by(status='open').all()
            for order in active_orders:
                # 重新將活躍訂單寫入 Redis，使用 hset 替代 hmset
                redis_client.hset(
                    f'group_order:{order.id}',
                    mapping={
                        'restaurant': str(order.restaurant),
                        'leader_id': str(order.leader_id),
                        'status': 'open'
                    }
                )
                
                # 同步該團購的所有用戶訂單
                user_orders = UserOrder.query.filter_by(group_order_id=order.id).all()
                for user_order in user_orders:
                    redis_client.hset(
                        f'group_order:{order.id}:orders',
                        user_order.user_id,
                        json.dumps(user_order.items)
                    )
                    
    except Exception as e:
        print(f"初始化資料庫時發生錯誤: {e}")

# 定時檢查並關閉到期的團購
def check_and_close_orders():
    print("檢查是否有需要自動閉團的團購...")
    closed_orders = db_manager.check_and_close_expired_orders()
    if closed_orders:
        print(f"已自動關閉 {len(closed_orders)} 個團購")

def get_user_closed_group_orders_summary(leader_id):
    """根據 leader_id 獲取該使用者開的已關閉團購的訂單資訊明細"""
    try:
        # 獲取所有該使用者開的已關閉團購
        closed_group_orders = GroupOrder.query.filter_by(leader_id=leader_id, status='closed').all()
        
        if not closed_group_orders:
            return "您目前沒有已截止的團購！"

        summary = "已關閉團購訂單明細：\n"
        summary += "=================\n"

        for order in closed_group_orders:
            restaurant = order.restaurant
            order_id = order.id
            
            # 獲取該團購的所有用戶訂單
            all_orders = db_manager.get_user_orders(order_id)
            if all_orders:
                # 統計訂單
                counter = Counter()
                for items in all_orders.values():
                    counter.update(items)

                # 生成訂單總結
                summary += f"【{restaurant}】 團購總結：\n"
                for item, count in counter.items():
                    summary += f"{item}: {count}份\n"
                
                # 加入個人訂單詳細資訊
                summary += "\n個人訂單明細：\n"
                for user_id, items in all_orders.items():
                    # 取得用戶名稱
                    user_name = get_user_name(user_id)
                    # 計算個人訂單項目
                    personal_counter = Counter(items)
                    personal_items = ", ".join([f"{item}*{count}" for item, count in personal_counter.items()])
                    summary += f"{user_name}：{personal_items}\n"
                summary += "=================\n"
            else:
                summary += f"{restaurant} 團購沒有任何訂單。\n"

        return summary

    except Exception as e:
        print(f"獲取團購訂單明細時發生錯誤: {e}")
        return "發生錯誤，無法獲取訂單明細。"

# ==============================================================================
#  訊息處理輔助函式
# ==============================================================================

def handle_start_group_selection(event, line_bot_api):
    """處理使用者輸入「開團」的請求，顯示可選餐廳的 Carousel Template。"""
    columns = []
    if not env_config.RESTAURANTS:
        reply_text = "目前沒有可供選擇的餐廳。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    for restaurant in env_config.RESTAURANTS:
        url = get_restaurant_image_url(restaurant)
        column = CarouselColumn(
            thumbnail_image_url=url,
            title=restaurant,
            text="點選開始開團",
            actions=[MessageAction(label="開始開團", text=f"{restaurant}開團")]
        )
        columns.append(column)

    if columns:
        carousel_template = CarouselTemplate(columns=columns)
        template_message = TemplateMessage(alt_text="選擇要開團的店家", template=carousel_template)
        line_bot_api.reply_message(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
        )
    else:
        reply_text = "無法生成餐廳選項，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def get_restaurant_image_url(restaurant_name: str) -> str:
    """
    根據餐廳名稱獲取對應的圖片 URL。
    會檢查設定檔中的菜單圖片名稱，並尋找對應的靜態圖片檔。
    如果找不到特定餐廳圖片，返回預設圖片 URL。
    """
    menu_image = env_config.MENU_DICT.get(restaurant_name)
    url = None
    if menu_image:
        image_extensions = ['jpg', 'png', 'jpeg']
        for ext in image_extensions:
            image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
            if os.path.exists(image_path):
                image_path = image_path.replace("\\", "/")
                url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                break
    
    if not url:
        url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
    
    return url

def handle_show_user_closed_groups(event, line_bot_api, user_id):
    """處理使用者輸入「我的開團」的請求，顯示該使用者已關閉的團購摘要。"""
    summary = get_user_closed_group_orders_summary(user_id)
    line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=summary)])
    )

def handle_create_group_intent(event, line_bot_api, text, user_id):
    """處理使用者輸入「xxx開團」的請求，嘗試創建新的團購。"""
    restaurant = text.replace("開團", "").strip()
    if not restaurant:
        reply_text = "請輸入餐廳名稱，例如：50嵐開團"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return
    
    if restaurant not in env_config.RESTAURANTS:
        reply_text = f"目前不支援 {restaurant}，可用餐廳：{', '.join(env_config.RESTAURANTS)}"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    # 檢查是否已經有此餐廳的活躍團購
    active_orders = db_manager.get_active_orders()
    existing_order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
    
    # 已有存在開團
    if existing_order:
        leader_id = existing_order['leader_id']
        leader_name = get_user_name(leader_id)
        reply_text = f"{restaurant} 團購已經開啟，開團者: {leader_name}，請先閉團再開新團！"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    # 創建新的團購
    try:
        group_order = db_manager.create_group_order(restaurant, user_id)
        
        # 準備時間選擇器
        now_utc = datetime.now(UTC)
        taiwan_time = now_utc + timedelta(hours=8)
        min_time = taiwan_time.strftime("%Y-%m-%dT%H:%M")
        
        quick_reply = QuickReply(items=[
            QuickReplyItem(
                action=DatetimePickerAction(
                    label="選擇閉團時間",
                    data=f"set_time_{group_order.id}",
                    mode="datetime",
                    min=min_time
                )
            )
        ])
        
        reply_message = TextMessage(
            text=f"{restaurant} 團購開啟，請選擇閉團時間：",
            quick_reply=quick_reply
        )
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[reply_message]))
        
    except Exception as e:
        app.logger.error(f"創建團購失敗: {e}")
        reply_text = "創建團購時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_close_group_selection(event, line_bot_api, user_id):
    """處理使用者輸入「閉團」的請求，顯示該使用者開啟的活躍團購供選擇。"""
    active_orders = db_manager.get_active_orders()
    user_groups = [order for order in active_orders if order['leader_id'] == user_id]

    if not user_groups:
        reply_text = "您目前沒有開團！"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    columns = []
    for order in user_groups:
        restaurant = order['restaurant']
        url = get_restaurant_image_url(restaurant)
        column = CarouselColumn(
            thumbnail_image_url=url,
            title=restaurant,
            text="點選結束此團購",
            actions=[PostbackAction(label="結束此團購", data=f"close_group_{restaurant}")]
        )
        columns.append(column)

    if columns:
        carousel_template = CarouselTemplate(columns=columns)
        template_message = TemplateMessage(alt_text="選擇要結束的團購", template=carousel_template)
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message]))
    else:
        reply_text = "無法生成團購選項，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_show_active_groups(event, line_bot_api):
    """處理使用者輸入「目前團購」的請求，顯示所有活躍中的團購。"""
    active_orders = db_manager.get_active_orders()
    if not active_orders:
        reply_text = "目前沒有進行中的團購！"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    columns = []
    for order in active_orders:
        restaurant = order['restaurant']
        leader_id = order['leader_id']
        leader_name = get_user_name(leader_id)
        close_time = order.get('close_time')
        
        # 計算剩餘時間
        time_remaining = ""
        if close_time:
            try:
                # 將字串轉換為 datetime 物件，並確保時區資訊
                close_time_dt = datetime.fromisoformat(close_time)
                if not close_time_dt.tzinfo:
                    # 如果沒有時區資訊，假設是台北時間
                    tw_tz = timezone(timedelta(hours=8))
                    close_time_dt = close_time_dt.replace(tzinfo=tw_tz)
                
                # 取得目前台北時間
                now = datetime.now(timezone(timedelta(hours=8)))
                
                # 計算時間差
                time_diff = close_time_dt - now
                
                if time_diff.total_seconds() > 0:
                    hours = int(time_diff.total_seconds() // 3600)
                    minutes = int((time_diff.total_seconds() % 3600) // 60)
                    
                    if hours > 0:
                        time_remaining = f"⏰ 剩 {hours} 小時 {minutes} 分鐘結單"
                    else:
                        if minutes < 30:
                            time_remaining = f"⚠️ 即將結單\n🔥 剩下{minutes}分鐘 🔥"
                        else:
                            time_remaining = f"⏰ 剩 {minutes} 分鐘結單"
                else:
                    time_remaining = "❌ 已結單 ❌"
            except ValueError as e:
                app.logger.error(f"無法解析閉團時間: {close_time}, 錯誤: {e}")
                time_remaining = "⏰ 時間格式錯誤"

        url = get_restaurant_image_url(restaurant)
        order_count = len(db_manager.get_user_orders(order['id']))
        
        column = CarouselColumn(
            thumbnail_image_url=url,
            title=f"【{restaurant}】",
            text=f"👥 開團者：{leader_name}\n{time_remaining}\n🛒 已有 {order_count} 人點餐",
            actions=[
                PostbackAction(label="加入此團購", data=f"select_group_{order['id']}"),
                PostbackAction(label="查看菜單", data=f"menu_{restaurant}")
            ]
        )
        columns.append(column)

    if columns:
        carousel_template = CarouselTemplate(columns=columns)
        template_message = TemplateMessage(alt_text="目前進行中的團購", template=carousel_template)
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message]))
    else:
        reply_text = "無法顯示團購資訊，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def parse_order_message(msg: str) -> dict:
    """
    解析點餐訊息，支援多種輸入格式
    
    支援的格式：
    - 我要點 珍奶（少冰半糖加波霸）
    - 我要點珍奶（半糖去冰）
    - 我要點 珍奶 半糖去冰
    - 點 珍奶
    - 珍奶
    
    Args:
        msg: 點餐訊息字串
    
    Returns:
        dict: 包含商品名稱和備註的字典
        {
            "item": "商品名稱",
            "note": "備註內容"  # 若無備註則為空字串
        }
    """
    # 移除所有多餘的空格
    msg = msg.strip()
    
    # 定義多個正規表達式模式來匹配不同格式
    patterns = [
        # 格式1：我要點 珍奶（少冰半糖加波霸）
        r'^(?:我要點|點)?\s*([^\s（(]+)(?:[（(]([^）)]+)[）)])?$',
        
        # 格式2：我要點 珍奶 半糖去冰
        r'^(?:我要點|點)?\s*([^\s]+)(?:\s+(.+))?$'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, msg)
        if match:
            item = match.group(1)
            note = match.group(2) if match.group(2) else ""
            
            # 清理可能的空白
            item = item.strip()
            note = note.strip()
            
            return {
                "item": item,
                "note": note
            }
    
    # 如果都沒有匹配到，假設整個訊息就是商品名稱
    return {
        "item": msg,
        "note": ""
    }

def handle_add_order_item(event, line_bot_api, text, user_id):
    """處理使用者輸入「我要點 xxx」的請求，將餐點加入使用者選擇的團購中。"""
    # 撈出團購編號
    selected_group = redis_client.get(f'user:{user_id}:selected_group')
    if not selected_group:
        reply_text = "請先輸入「目前團購」選擇團購！"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    group_order_id = selected_group
    # 撈出團購資訊
    active_orders = db_manager.get_active_orders()
    # 撈出此團購所有訂單
    order = next((order for order in active_orders if order['id'] == group_order_id), None)
    
    if not order:
        reply_text = "您選擇的團購已不存在！"
        redis_client.delete(f'user:{user_id}:selected_group')
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    restaurant = order['restaurant']
    
    # 使用新的解析方法處理點餐訊息
    order_info = parse_order_message(text)
    if not order_info["item"]:
        reply_text = "請輸入餐點名稱，例如：我要點 珍珠奶茶(微糖微冰)"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        return

    # 組合商品名稱和備註
    meal_text = order_info["item"]
    if order_info["note"]:
        meal_text = f"{meal_text}({order_info['note']})"

    try:
        # 獲取現有訂單
        existing_order = db_manager.get_user_order(group_order_id, user_id)
        all_meals = existing_order + [meal_text] if existing_order else [meal_text]
        
        # 更新訂單
        db_manager.add_user_order(group_order_id, user_id, all_meals)
        
        # 使用 Counter 統計所有品項數量
        meal_counter = Counter(all_meals)
        order_summary = "、".join([f"{item}*{count}" for item, count in meal_counter.items()])
        reply_text = f"已將 {meal_text} 加入您在 {restaurant} 的訂單中！\n目前訂單：{order_summary}"
        
    except Exception as e:
        app.logger.error(f"更新訂單失敗: {e}")
        reply_text = "更新訂單時發生錯誤，請稍後再試。"

    line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_close_group_action(event, line_bot_api, restaurant, user_id):
    """
    處理關閉特定團購的請求。
    檢查使用者權限並生成訂單摘要。
    """
    try:
        # 檢查是否為團購發起人
        active_orders = db_manager.get_active_orders()
        order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
        
        if not order:
            reply_text = f"找不到 {restaurant} 的團購！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        if order['leader_id'] != user_id:
            reply_text = "只有開團者可以關閉團購！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 獲取所有訂單
        order_id = order['id']
        all_orders = db_manager.get_user_orders(order_id)
        
        # 生成訂單摘要
        summary = f"【{restaurant}】團購訂單明細：\n=================\n"
        
        if all_orders:
            # 統計總訂單
            counter = Counter()
            for items in all_orders.values():
                counter.update(items)
                
            # 添加總訂單統計
            summary += "總訂單統計：\n"
            for item, count in counter.items():
                summary += f"{item}: {count}份\n"
            
            # 添加個人訂單明細
            summary += "\n個人訂單明細：\n"
            for user_id, items in all_orders.items():
                user_name = get_user_name(user_id)
                personal_counter = Counter(items)
                personal_items = ", ".join([f"{item}*{count}" for item, count in personal_counter.items()])
                summary += f"{user_name}：{personal_items}\n"
        else:
            summary += "沒有任何訂單。\n"
            
        summary += "=================\n團購已關閉！"
        
        # 關閉團購
        db_manager.close_group_order(order_id)
        
        # 發送訂單摘要
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=summary)]))
        
    except Exception as e:
        app.logger.error(f"關閉團購時發生錯誤: {e}")
        reply_text = "關閉團購時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_select_group_action(event, line_bot_api, group_order_id, user_id):
    """
    處理使用者選擇加入特定團購的請求。
    設置使用者當前選擇的團購，並顯示菜單。
    """
    try:
        # 檢查團購是否存在且開放中
        active_orders = db_manager.get_active_orders()
        order = next((order for order in active_orders if order['id'] == group_order_id), None)
        
        if not order:
            reply_text = "此團購已不存在或已關閉！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 設置使用者選擇的團購
        # 設置查詢的key
        redis_client.set(f'user:{user_id}:selected_group', group_order_id)
        
        # 獲取餐廳菜單圖片
        restaurant = order['restaurant']
        menu_image = env_config.MENU_DICT.get(restaurant)
        
        if menu_image:
            # 發送菜單圖片和使用說明
            image_url = get_restaurant_image_url(restaurant)
            messages = [
                ImageMessage(original_content_url=image_url, preview_image_url=image_url),
                TextMessage(text=f"您已選擇 {restaurant} 的團購！\n請輸入「我要點 xxx」來點餐。\n例如：我要點 珍珠奶茶(微糖微冰)")
            ]
        else:
            messages = [TextMessage(text=f"您已選擇 {restaurant} 的團購！\n請輸入「我要點 xxx」來點餐。")]
            
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=messages))
        
    except Exception as e:
        app.logger.error(f"選擇團購時發生錯誤: {e}")
        reply_text = "選擇團購時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_user_order_summary(event, line_bot_api):
    try:
        user_id = event.source.user_id
        active_orders = db_manager.get_active_orders()
        
        if not active_orders:
            reply_text = "目前沒有進行中的團購！"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
            return

        # 收集使用者的所有訂單
        user_orders_data = []
        for order in active_orders:
            user_order_items = db_manager.get_user_order(order['id'], user_id)
            if user_order_items:
                user_orders_data.append({
                    'restaurant': order['restaurant'],
                    'order_id': order['id'],
                    'items': user_order_items
                })

        if not user_orders_data:
            reply_text = "您目前沒有任何訂單！"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
            return

        # 創建 carousel 內容
        bubbles = []
        for order_data in user_orders_data:
            # 統計訂單項目並產生摘要文字
            counter = Counter(order_data['items'])
            order_summary_text = "\n".join([f"- {item}: {count} 份" for item, count in counter.items()])
            if not order_summary_text:
                order_summary_text = "您的訂單是空的"
            
            # 創建每個餐廳的 bubble
            bubble = {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": get_restaurant_image_url(order_data['restaurant']),
                    "size": "full",
                    "aspectRatio": "20:13",
                    "aspectMode": "cover"
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md", # 調整間距
                    "contents": [
                        {
                            "type": "text",
                            "text": f"【{order_data['restaurant']}】",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#000000"
                        },
                        {
                            "type": "separator",
                            "margin": "lg" # 調整間距
                        },
                        {
                            "type": "text",
                            "text": "您的訂單內容：",
                            "weight": "bold",
                            "margin": "lg", # 調整間距
                            "size": "md"
                        },
                        {
                            "type": "text",
                            "text": order_summary_text,
                            "wrap": True,
                            "margin": "sm", # 調整間距
                            "size": "sm",
                            "color": "#555555"
                        }
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "color": "#4CAF50", # 綠色
                            "height": "sm",
                            "action": {
                                "type": "postback",
                                "label": "✏️ 修改訂單",
                                "data": f"action=edit_order&group_order_id={order_data['order_id']}",
                                "displayText": f"修改 {order_data['restaurant']} 訂單"
                            }
                        },
                        {
                            "type": "button",
                            "style": "primary",
                            "color": "#F44336", # 紅色 (調整了顏色)
                            "height": "sm",
                            "action": {
                                "type": "postback",
                                "label": "🗑️ 清空此訂單",
                                "data": f"action=clear_my_order&group_order_id={order_data['order_id']}",
                                "displayText": f"確定要清空 {order_data['restaurant']} 的訂單嗎？"
                            }
                        }
                    ]
                }
            }
            bubbles.append(bubble)

        # 創建 carousel flex message
        carousel_flex = {
            "type": "carousel",
            "contents": bubbles
        }

        # 發送 flex message
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[FlexMessage(
                    alt_text="您的訂單明細",
                    contents=FlexContainer.from_json(json.dumps(carousel_flex))
                )]
            )
        )

    except Exception as e:
        app.logger.error(f"顯示訂單摘要時發生錯誤: {e}")
        reply_text = "顯示訂單摘要時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

def handle_show_menu_action(event, line_bot_api, restaurant):
    """
    處理顯示餐廳菜單的請求。
    """
    try:
        menu_image = env_config.MENU_DICT.get(restaurant)
        if menu_image:
            image_url = get_restaurant_image_url(restaurant)
            messages = [ImageMessage(original_content_url=image_url, preview_image_url=image_url)]
        else:
            messages = [TextMessage(text=f"抱歉，目前沒有 {restaurant} 的菜單圖片。")]
            
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=messages))
        
    except Exception as e:
        app.logger.error(f"顯示菜單時發生錯誤: {e}")
        reply_text = "顯示菜單時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_delete_order_action(event, line_bot_api, group_order_id, user_id):
    """
    處理刪除使用者訂單的請求。
    """
    try:
        # 檢查團購是否存在且開放中
        active_orders = db_manager.get_active_orders()
        order = next((order for order in active_orders if order['id'] == group_order_id), None)
        
        if not order:
            reply_text = "此團購已不存在或已關閉！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 刪除訂單
        db_manager.delete_user_order(group_order_id, user_id)
        
        reply_text = f"已刪除您在 {order['restaurant']} 的所有訂單！"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        
    except Exception as e:
        app.logger.error(f"刪除訂單時發生錯誤: {e}")
        reply_text = "刪除訂單時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_set_close_time_action(event, line_bot_api, group_order_id, user_id, params):
    """
    處理設置團購閉團時間的請求。
    """
    try:
        app.logger.info(f"設置閉團時間: group_order_id={group_order_id}, user_id={user_id}")
        
        # 確保 group_order_id 是字符串類型
        group_order_id = str(group_order_id)
        
        # 檢查團購是否存在且開放中
        active_orders = db_manager.get_active_orders()
        app.logger.info(f"活躍團購列表: {active_orders}")
        
        order = next((order for order in active_orders if str(order['id']) == group_order_id), None)
        
        if not order:
            app.logger.error(f"找不到團購: {group_order_id}")
            reply_text = "此團購已不存在或已關閉！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        if order['leader_id'] != user_id:
            app.logger.error(f"權限錯誤: user_id={user_id} 不是團購 leader_id={order['leader_id']}")
            reply_text = "只有開團者可以設置閉團時間！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 解析並設置閉團時間
        close_time = params.get('datetime')
        if not close_time:
            app.logger.error("未提供閉團時間")
            reply_text = "請選擇有效的閉團時間！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 將時間轉換為 UTC
        try:
            close_time_dt = datetime.fromisoformat(close_time)
            close_time_utc = close_time_dt.astimezone(UTC)
            app.logger.info(f"設置的閉團時間: {close_time_utc}")
        except Exception as e:
            app.logger.error(f"時間格式錯誤: {e}")
            reply_text = "時間格式錯誤，請重新選擇！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
        
        # 更新閉團時間
        if db_manager.set_group_order_close_time(group_order_id, close_time_utc):
            # 格式化顯示時間（轉換為台灣時間）
            tw_time = close_time_dt.strftime("%Y-%m-%d %H:%M")
            reply_text = f"已設置 {order['restaurant']} 的閉團時間為：{tw_time}"
            app.logger.info(f"成功設置閉團時間: {reply_text}")
        else:
            app.logger.error("更新閉團時間失敗")
            reply_text = "設置閉團時間失敗，請稍後再試。"
        
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        
    except Exception as e:
        app.logger.error(f"設置閉團時間時發生錯誤: {e}")
        reply_text = "設置閉團時間時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_custom_close_time_input(event, line_bot_api, text, user_id):
    """
    處理使用者手動輸入閉團時間的請求。
    """
    try:
        # 獲取等待設置時間的團購 ID
        group_order_id = redis_client.get(f'waiting_time_input:{user_id}')
        if not group_order_id:
            reply_text = "請先選擇要設置閉團時間的團購！"
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 檢查團購是否存在且開放中
        active_orders = db_manager.get_active_orders()
        order = next((order for order in active_orders if order['id'] == group_order_id), None)
        
        if not order:
            reply_text = "此團購已不存在或已關閉！"
            redis_client.delete(f'waiting_time_input:{user_id}')
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        if order['leader_id'] != user_id:
            reply_text = "只有開團者可以設置閉團時間！"
            redis_client.delete(f'waiting_time_input:{user_id}')
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
            return
            
        # 嘗試解析時間字串
        try:
            # 支援多種時間格式
            time_formats = [
                "%Y-%m-%d %H:%M",
                "%m/%d %H:%M",
                "%H:%M"
            ]
            
            close_time_dt = None
            for fmt in time_formats:
                try:
                    if fmt == "%H:%M":
                        # 如果只有時間，假設是今天
                        now = datetime.now(timezone(timedelta(hours=8)))
                        time_obj = datetime.strptime(text, fmt)
                        close_time_dt = now.replace(
                            hour=time_obj.hour,
                            minute=time_obj.minute,
                            second=0,
                            microsecond=0
                        )
                    else:
                        close_time_dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
                    
            if not close_time_dt:
                raise ValueError("無效的時間格式")
                
            # 設置時區為台灣時間
            tw_tz = timezone(timedelta(hours=8))
            close_time_dt = close_time_dt.replace(tzinfo=tw_tz)
            
            # 轉換為 UTC 時間
            close_time_utc = close_time_dt.astimezone(UTC)
            
            # 檢查時間是否在未來
            if close_time_utc <= datetime.now(UTC):
                reply_text = "閉團時間必須在未來！"
                line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
                return
                
            # 更新閉團時間
            db_manager.set_group_order_close_time(group_order_id, close_time_utc)
            
            # 清除等待狀態
            redis_client.delete(f'waiting_time_input:{user_id}')
            
            # 格式化顯示時間
            tw_time = close_time_dt.strftime("%Y-%m-%d %H:%M")
            reply_text = f"已設置 {order['restaurant']} 的閉團時間為：{tw_time}"
            
        except ValueError:
            reply_text = (
                "請輸入有效的時間格式：\n"
                "1. YYYY-MM-DD HH:MM\n"
                "2. MM/DD HH:MM\n"
                "3. HH:MM（今天）"
            )
            
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        
    except Exception as e:
        app.logger.error(f"設置自定義閉團時間時發生錯誤: {e}")
        reply_text = "設置閉團時間時發生錯誤，請稍後再試。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

def handle_edit_order(event, line_bot_api, group_order_id, user_id):
    """處理修改訂單的請求"""
    try:
        # 獲取用戶的訂單
        user_order = db_manager.get_user_order(group_order_id, user_id)
        if not user_order:
            reply_text = "找不到您的訂單！"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
            return

        # 統計訂單項目
        counter = Counter(user_order)
        
        # 創建修改訂單的 Flex Message
        items_components = []
        for item, count in counter.items():
            item_name = item
            note = ""
            if "(" in item and ")" in item:
                item_name = item[:item.find("(")]
                note = item[item.find("(")+1:item.find(")")]
            
            items_components.append({
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": item_name,
                        "size": "md",
                        "color": "#555555",
                        "weight": "bold"
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "style": "secondary",
                                "height": "sm",
                                "action": {
                                    "type": "postback",
                                    "label": "-",
                                    "data": f"action=decrease_item&group_order_id={group_order_id}&item={item}"
                                }
                            },
                            {
                                "type": "text",
                                "text": str(count),
                                "size": "md",
                                "color": "#111111",
                                "align": "center",
                                "gravity": "center",
                                "flex": 1
                            },
                            {
                                "type": "button",
                                "style": "secondary",
                                "height": "sm",
                                "action": {
                                    "type": "postback",
                                    "label": "+",
                                    "data": f"action=increase_item&group_order_id={group_order_id}&item={item}"
                                }
                            }
                        ]
                    },
                    {
                        "type": "text",
                        "text": f"備註：{note if note else '無'}",
                        "size": "sm",
                        "color": "#888888",
                        "wrap": True,
                        "margin": "sm"
                    },
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "margin": "xs",
                        "action": {
                            "type": "postback",
                            "label": "✏️ 編輯備註",
                            "data": f"action=prompt_update_note&group_order_id={group_order_id}&item={item}"
                        }
                    }
                ]
            })

        # 創建修改訂單的 Flex Message
        edit_flex = {
            "type": "bubble",
            "body": {
                "type": "box", "layout": "vertical", "contents": [
                    { "type": "text", "text": "修改訂單", "weight": "bold", "size": "xl" },
                    { "type": "separator", "margin": "xxl" },
                    {
                        "type": "box", "layout": "vertical", "margin": "xl", "spacing": "sm",
                        "contents": items_components
                    }
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {
                        "type": "button", "style": "primary", "color": "#4CAF50",
                        "action": {
                            "type": "postback", "label": "💾完成修改",
                            "data": f"action=save_edit&group_order_id={group_order_id}"
                        }
                    }
                ]
            }
        }

        # 發送修改訂單的 Flex Message
        try:
                line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[FlexMessage(alt_text="修改訂單",
                                        contents=FlexContainer.from_json(json.dumps(edit_flex)))]
                )
            )
        except Exception as e:
            app.logger.error(f"發送修改訂單介面時發生錯誤: {e}")
            # 如果回覆失敗，嘗試使用 push message
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[FlexMessage(
                        alt_text="修改訂單",
                        contents=FlexContainer.from_json(json.dumps(edit_flex))
                    )]
                )
            )

    except Exception as e:
        app.logger.error(f"顯示修改訂單介面時發生錯誤: {e}")
        reply_text = "顯示修改訂單介面時發生錯誤，請稍後再試。"
        try:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        except Exception as e:
            app.logger.error(f"發送錯誤訊息時發生錯誤: {e}")
            # 如果回覆失敗，嘗試使用 push message
            line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=reply_text)]
                )
            )

def handle_increase_item(event, line_bot_api, group_order_id, item):
    """處理增加商品數量的請求"""
    try:
        user_id = event.source.user_id
        user_order = db_manager.get_user_order(group_order_id, user_id)
        if not user_order:
            return

        # 增加商品數量
        user_order.append(item)
        db_manager.add_user_order(group_order_id, user_id, user_order)
        
        # 重新顯示修改訂單介面
        handle_edit_order(event, line_bot_api, group_order_id, user_id)

    except Exception as e:
        app.logger.error(f"增加商品數量時發生錯誤: {e}")

def handle_decrease_item(event, line_bot_api, group_order_id, item):
    """處理減少商品數量的請求"""
    try:
        user_id = event.source.user_id
        user_order = db_manager.get_user_order(group_order_id, user_id)
        if not user_order:
            return

        # 減少商品數量
        if item in user_order:
            user_order.remove(item)
            db_manager.add_user_order(group_order_id, user_id, user_order)
        
        # 重新顯示修改訂單介面
        handle_edit_order(event, line_bot_api, group_order_id, user_id)

    except Exception as e:
        app.logger.error(f"減少商品數量時發生錯誤: {e}")

def handle_update_note(event, line_bot_api, group_order_id, item, new_note):
    """處理更新商品備註的請求"""
    try:
        user_id = event.source.user_id
        user_order = db_manager.get_user_order(group_order_id, user_id)
        if not user_order:
            # 如果找不到訂單，可能狀態已過期，發送提示訊息
            reply_text = "無法更新備註，找不到原始訂單。"
            try:
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
                )
            except Exception as inner_e:
                app.logger.error(f"發送更新備註錯誤訊息失敗: {inner_e}")
            return

        # 更新商品備註
        item_name = item
        if "(" in item and ")" in item:
            item_name = item[:item.find("(")]
        
        # 清理新的備註，移除前後空格
        new_note_cleaned = new_note.strip()
        
        new_item = f"{item_name}({new_note_cleaned})" if new_note_cleaned else item_name
        
        # 替換舊的商品
        # 需要找到第一個匹配的 item 進行替換
        new_order = list(user_order) # 創建副本以修改
        try:
            index_to_replace = new_order.index(item)
            new_order[index_to_replace] = new_item
        except ValueError:
            # 如果原始項目找不到 (理論上不應發生，因為是從狀態來的)
            app.logger.error(f"更新備註時找不到原始項目 '{item}' 在訂單中")
            reply_text = f"更新備註失敗，找不到原始項目 '{item_name}'。"
            try:
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
                )
            except Exception as inner_e:
                app.logger.error(f"發送更新備註失敗訊息失敗: {inner_e}")
            return

        db_manager.add_user_order(group_order_id, user_id, new_order)
        
        # 回覆確認訊息並重新顯示修改訂單介面
        # 注意：這裡不再使用 reply_token，因為可能已經過期
        # 而且 handle_edit_order 會自己處理 reply 或 push
        handle_edit_order(event, line_bot_api, group_order_id, user_id)

    except Exception as e:
        app.logger.error(f"更新商品備註時發生錯誤: {e}")
        # 嘗試發送錯誤訊息
        reply_text = "更新備註時發生錯誤，請稍後再試。"
        try:
            # 由於 reply_token 可能已失效，優先嘗試 push
            line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=[TextMessage(text=reply_text)])
            )
        except Exception as push_error:
             app.logger.error(f"推送更新備註錯誤訊息失敗: {push_error}")

def handle_save_edit(event, line_bot_api, group_order_id):
    """處理儲存修改的請求"""
    try:
        user_id = event.source.user_id
        reply_text = "訂單修改已儲存！"
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )
        
        # 重新顯示訂單摘要
        handle_user_order_summary(event, line_bot_api)

    except Exception as e:
        app.logger.error(f"儲存修改時發生錯誤: {e}")

# ==============================================================================
#  應用程式主入口點
# ==============================================================================

if __name__ == "__main__":
    # --- 應用程式啟動前的準備工作 ---
    with app.app_context():
        initialize_redis_and_db()
        
        # 檢查並設置 Rich Menu
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                response = line_bot_api.get_default_rich_menu_id()
                default_rich_menu_id = response.rich_menu_id if hasattr(response, 'rich_menu_id') else None
                
                if not default_rich_menu_id:
                    app.logger.info("沒有找到預設的 Rich Menu，嘗試創建...")
                    create_rich_menu()
                else:
                    app.logger.info(f"已存在預設的 Rich Menu ID: {default_rich_menu_id}")
        except Exception as e:
            app.logger.error(f"檢查或創建 Rich Menu 時發生錯誤: {e}。嘗試強制創建...")
            create_rich_menu()

    # --- 啟動定時任務 ---
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_close_orders, 'interval', minutes=env_config.SCHEDULER_INTERVAL_MINUTES, id='check_orders_job')
    scheduler.start()
    app.logger.info(f"定時任務已啟動，每 {env_config.SCHEDULER_INTERVAL_MINUTES} 分鐘檢查一次過期團購。")

    # --- 啟動 Flask Web 伺服器 ---
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), debug=app.debug)