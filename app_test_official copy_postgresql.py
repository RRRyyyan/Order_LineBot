from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,
    TemplateMessage,
    ButtonsTemplate,
    CarouselTemplate,
    CarouselColumn,
    PostbackAction,
    RichMenuSize,
    RichMenuRequest,
    RichMenuArea,
    RichMenuBounds,
    MessageAction,
    URIAction,
    QuickReply,
    QuickReplyItem,
    DatetimePickerAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    FollowEvent,
    PostbackEvent,
    TextMessageContent,
)
from collections import Counter
import re
import os
import requests
import json
import time
from redis import Redis
from datetime import datetime, timedelta, UTC
from apscheduler.schedulers.background import BackgroundScheduler

# 導入 Config 文件中的配置
from config import get_config, Config, OrderConfig, LineBotConfig
from database import db, DatabaseManager, GroupOrder, UserOrder 

from flask_sqlalchemy import SQLAlchemy

# 初始化 Flask 應用
app = Flask(__name__, static_folder='static')

# 獲取環境配置 (預設為 development)
env_config = get_config('development' if app.debug else 'production')

# 配置 SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = env_config.SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# 初始化 Redis 和資料庫管理器
redis_client = Redis.from_url(env_config.REDIS_URL)
db_manager = DatabaseManager(redis_client)

# LINE Bot 配置
configuration = Configuration(
    access_token=env_config.CHANNEL_ACCESS_TOKEN
)
line_handler = WebhookHandler(env_config.CHANNEL_SECRET)

# 快取用戶名稱
user_names = {}

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        line_handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@line_handler.add(FollowEvent)
def handle_follow(event):
    print(f"Got {event.type} event")

def handle_open_group(event, text, user_id):
    """
    處理開團請求的函式。
    參數:
    event (Event): Line Bot 的事件物件，包含回覆 token 等資訊。
    text (str): 使用者輸入的文字訊息。
    user_id (str): 發送請求的使用者 ID。
    """
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        restaurant = text.replace("開團", "").strip()
        if not restaurant:
            reply_text = "請輸入餐廳名稱，例如：50嵐開團"
        elif restaurant not in env_config.RESTAURANTS:
            reply_text = f"目前不支援 {restaurant}，可用餐廳：{', '.join(env_config.RESTAURANTS)}"
        else:
            # 檢查是否已經有此餐廳的開團
            active_orders = db_manager.get_active_orders()
            existing_order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
            
            if existing_order:
                leader_id = existing_order['leader_id']
                leader_name = get_user_name(leader_id)
                reply_text = f"{restaurant} 團購已經開啟，開團者: {leader_name}，請先閉團再開新團！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                # 創建新的團購
                group_order = db_manager.create_group_order(restaurant, user_id)
                
                # 使用 Datetime Picker Template
                buttons_template = ButtonsTemplate(
                    title=f"{restaurant} 團購",
                    text="請選擇閉團時間",
                    actions=[
                        DatetimePickerAction(
                            label="選擇時間",
                            data=f"set_time_{group_order.id}",
                            mode="time"
                        )
                    ]
                )
                template_message = TemplateMessage(alt_text="選擇閉團時間", template=buttons_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                )
                return

@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id 
        text = event.message.text

        if text == "開團":
            # 顯示餐廳選擇的 Carousel Template
            columns = []
            for restaurant in env_config.RESTAURANTS:
                # 支援的圖片格式
                image_extensions = ['jpg', 'png', 'jpeg']
                menu_image = env_config.MENU_DICT.get(restaurant)
                if not menu_image:
                    reply_text = "目前無此餐廳的菜單資訊！"
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)],
                        )
                    )
                    return
                
                # 找到對應的圖片檔案
                url = None
                for ext in image_extensions:
                    image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                    if os.path.exists(image_path):
                        image_path = image_path.replace("\\", "/")
                        url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                        break
                
                if not url:
                    url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
                
                column = CarouselColumn(
                    thumbnail_image_url=url,
                    title=restaurant,
                    text="點選開始開團",
                    actions=[MessageAction(label="開始開團", text=f"{restaurant}開團")]
                )
                columns.append(column)
            
            carousel_template = CarouselTemplate(columns=columns)
            template_message = TemplateMessage(alt_text="選擇要開團的店家", template=carousel_template)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
            )
        elif text.endswith("開團"):
            handle_open_group(event, text, user_id)
        elif text == "閉團":
            # 顯示用戶已開的團購
            active_orders = db_manager.get_active_orders()
            user_groups = [order for order in active_orders if order['leader_id'] == user_id]
            
            if not user_groups:
                reply_text = "你目前沒有開團！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                columns = []
                for order in user_groups:
                    restaurant = order['restaurant']
                    # 支援的圖片格式
                    image_extensions = ['jpg', 'png', 'jpeg']
                    menu_image = env_config.MENU_DICT.get(restaurant)
                    if not menu_image:
                        continue
                    
                    url = None
                    for ext in image_extensions:
                        image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                        if os.path.exists(image_path):
                            image_path = image_path.replace("\\", "/")
                            url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                            break
                    
                    if not url:
                        url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
                    
                    column = CarouselColumn(
                        thumbnail_image_url=url,
                        title=restaurant,
                        text="點選結束此團購",
                        actions=[PostbackAction(label="結束此團購", data=f"close_group_{restaurant}")]
                    )
                    columns.append(column)

                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateMessage(alt_text="選擇要結束的團購", template=carousel_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                )
        elif text.endswith("閉團") and text != "閉團":
            restaurant = text.replace("閉團", "").strip()
            if not restaurant or restaurant not in env_config.RESTAURANTS:
                reply_text = "請輸入正確的餐廳名稱，例如：50嵐閉團"
            else:
                if db_manager.close_group_order(restaurant, user_id):
                    # 獲取所有訂單，統計總結
                    active_orders = db_manager.get_active_orders()
                    order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
                    if order:
                        all_orders = db_manager.get_user_orders(order['id'])
                        if all_orders:
                            # 統計訂單
                            counter = Counter()
                            for items in all_orders.values():
                                counter.update(items)
                            
                            # 生成訂單總結
                            summary = f"{restaurant} 團購總結：\n"
                            summary += "=================\n"
                            for item, count in counter.items():
                                summary += f"{item}: {count}份\n"
                            
                            # 加入個人訂單詳細資訊
                            summary += "\n個人訂單明細：\n"
                            summary += "=================\n"
                            for user_id, items in all_orders.items():
                                # 取得用戶名稱
                                user_name = get_user_name(user_id)
                                # 計算個人訂單項目
                                personal_counter = Counter(items)
                                personal_items = ", ".join([f"{item}*{count}" for item, count in personal_counter.items()])
                                summary += f"{user_name}：{personal_items}\n"
                            
                            reply_text = f"{restaurant} 團購已關閉！\n\n{summary}"
                        else:
                            reply_text = f"{restaurant} 團購已關閉，但沒有訂單。"
                    else:
                        reply_text = f"{restaurant} 團購已關閉。"
                else:
                    reply_text = "只有開團者可以閉團！"
                
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
        elif text == "目前團購":
            active_orders = db_manager.get_active_orders()
            if not active_orders:
                reply_text = "目前沒有進行中的團購！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                columns = []
                for order in active_orders:
                    restaurant = order['restaurant']
                    leader_id = order['leader_id']
                    leader_name = get_user_name(leader_id)  # 使用 get_user_name 取得真實名稱                    
                    # 支援的圖片格式
                    image_extensions = ['jpg', 'png', 'jpeg']
                    menu_image = env_config.MENU_DICT.get(restaurant)
                    if not menu_image:
                        continue
                    
                    url = None
                    for ext in image_extensions:
                        image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                        if os.path.exists(image_path):
                            image_path = image_path.replace("\\", "/")
                            url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                            break
                    
                    if not url:
                        url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
                    
                    column = CarouselColumn(
                        thumbnail_image_url=url,
                        title=restaurant,
                            text=f"開團者: {leader_name}\n開團中 - 已有{len(db_manager.get_user_orders(order['id']))}人點餐",
                        actions=[
                            PostbackAction(label="加入此團購", data=f"select_group_{order['id']}"),
                            PostbackAction(label="菜單價目表", data=f"menu_{restaurant}", text=f"查看{restaurant}的菜單"),
                        ]
                    )
                    columns.append(column)

                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateMessage(alt_text="選擇要加入的團購", template=carousel_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                )
        elif text.startswith("我要點"):
            # 檢查用戶是否已選擇團購
            selected_group = redis_client.get(f'user:{user_id}:selected_group')
            if not selected_group:
                reply_text = "請先輸入「目前團購」選擇團購！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
                return
            
            group_order_id = selected_group.decode()
            active_orders = db_manager.get_active_orders()
            order = next((order for order in active_orders if order['id'] == group_order_id), None)
            
            if not order:
                reply_text = "您選擇的團購已不存在！"
                redis_client.delete(f'user:{user_id}:selected_group')
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
                return
            
            restaurant = order['restaurant']
            meal_text = text.replace("我要點", "").strip()
            if not meal_text:
                reply_text = "請輸入餐點名稱，例如：我要點 珍珠奶茶(微糖微冰)"
            else:
                # 分割點餐品項
                new_meals = [meal.strip() for meal in re.split(r"[ ;,、]", meal_text) if meal.strip()]
                
                # 獲取現有訂單
                existing_order = db_manager.get_user_order(group_order_id, user_id)
                if existing_order:
                    # 合併現有訂單和新訂單
                    all_meals = existing_order + new_meals
                    # 使用 Counter 統計所有品項數量
                    meal_counter = Counter(all_meals)
                    # 格式化輸出，將每個品項加上數量
                    order_summary = "、".join([f"{item}*{count}" for item, count in meal_counter.items()])
                    reply_text = f"已將 {', '.join(new_meals)} 加入您在 {restaurant} 的訂單中！\n目前訂單：{order_summary}"
                else:
                    all_meals = new_meals
                    reply_text = f"已記錄 {', '.join(new_meals)} 到 {restaurant} 團購！"
                
                # 更新訂單
                db_manager.add_user_order(group_order_id, user_id, all_meals)
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif text == "我的訂單":
            active_orders = db_manager.get_active_orders()
            if not active_orders:
                reply_text = "目前沒有進行中的團購！"
                line_bot_api.reply_message(

                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                user_orders = []
                for order in active_orders:
                    user_order = db_manager.get_user_order(order['id'], user_id)
                    if user_order:
                        user_orders.append({
                            'restaurant': order['restaurant'],
                            'order_id': order['id'],
                            'items': user_order
                        })
                
                if not user_orders:
                    reply_text = "您目前沒有訂單！"
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)],
                        )
                    )
                else:
                    columns = []
                    for order in user_orders:
                        restaurant = order['restaurant']
                        items = order['items']
                        
                        # 支援的圖片格式
                        image_extensions = ['jpg', 'png', 'jpeg']
                        menu_image = env_config.MENU_DICT.get(restaurant)
                        if not menu_image:
                            continue
                        
                        url = None
                        for ext in image_extensions:
                            image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                            if os.path.exists(image_path):
                                image_path = image_path.replace("\\", "/")
                                url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                                break
                        
                        if not url:
                            url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
                        
                        items_text = "\n".join([f"{item}: {count}份" for item, count in Counter(items).items()])
                        column = CarouselColumn(
                            thumbnail_image_url=url,
                            title=restaurant,
                            text=items_text if items_text else "無訂單項目",
                            actions=[
                                PostbackAction(label="修改訂單", data=f"edit_order_{order['order_id']}"),
                                PostbackAction(label="刪除訂單", data=f"delete_order_{order['order_id']}")
                            ]
                        )
                        columns.append(column)

                    carousel_template = CarouselTemplate(columns=columns)
                    template_message = TemplateMessage(alt_text="您的訂單", template=carousel_template)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                    )

        # 處理自訂時間輸入
        elif redis_client.get(f'waiting_time_input:{user_id}'):
            try:
                minutes = int(text)
                if minutes <= 0 or minutes > 1440:  # 限制在1-1440分鐘內（24小時）
                    reply_text = "請輸入1-1440之間的有效數字！"
                else:
                    group_order_id = redis_client.get(f'waiting_time_input:{user_id}').decode()
                    close_time = datetime.now(UTC) + timedelta(minutes=minutes)
                    if db_manager.set_group_order_close_time(group_order_id, close_time):
                        reply_text = f"已設定 {minutes} 分鐘後自動閉團！"
                    else:
                        reply_text = "設定閉團時間失敗，團購可能已不存在。"
                    redis_client.delete(f'waiting_time_input:{user_id}')
            except ValueError:
                reply_text = "請輸入有效的數字！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

@line_handler.add(PostbackEvent)
def handle_postback(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        data = event.postback.data
        user_id = event.source.user_id

        if data.startswith("close_group_"):
            restaurant = data.replace("close_group_", "")
            if db_manager.close_group_order(restaurant, user_id):
                # 獲取所有訂單並生成總結
                active_orders = db_manager.get_active_orders()
                order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
                if order:
                    all_orders = db_manager.get_user_orders(order['id'])
                    if all_orders:
                        # 統計訂單
                        counter = Counter()
                        for items in all_orders.values():
                            counter.update(items)
                        
                        # 生成訂單總結
                        summary = f"{restaurant} 團購總結：\n"
                        for item, count in counter.items():
                            summary += f"{item}: {count}份\n"
                        
                        reply_text = f"{restaurant} 團購已關閉！\n\n{summary}"
                    else:
                        reply_text = f"{restaurant} 團購已關閉，但沒有訂單。"
                else:
                    reply_text = f"{restaurant} 團購已關閉。"
            else:
                reply_text = "只有開團者可以閉團！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data.startswith("select_group_"):
            group_order_id = data.replace("select_group_", "")
            active_orders = db_manager.get_active_orders()
            order = next((order for order in active_orders if order['id'] == group_order_id), None)
            if order:
                # 儲存用戶選擇的團購
                redis_client.set(f'user:{user_id}:selected_group', group_order_id)
                reply_text = f"您已選擇 {order['restaurant']} 的團購，請輸入「我要點 餐點名稱(備註)」開始點餐！"
            else:
                reply_text = "此團購已不存在！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data.startswith("edit_order_"):
            group_order_id = data.replace("edit_order_", "")
            active_orders = db_manager.get_active_orders()
            order = next((order for order in active_orders if order['id'] == group_order_id), None)
            
            if order:
                # 儲存用戶選擇的團購
                redis_client.set(f'user:{user_id}:selected_group', group_order_id)
                reply_text = f"您已選擇修改 {order['restaurant']} 的訂單，請輸入「我要點 餐點名稱(備註)」重新點餐！"
            else:
                reply_text = "此團購已不存在！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data.startswith("menu_"):
            restaurant = data.replace("menu_", "")
            
            # 檢查餐廳是否存在
            menu_image = env_config.MENU_DICT.get(restaurant)
            if not menu_image:
                reply_text = "目前無此餐廳的菜單資訊！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
                return
            
            # 支援的圖片格式
            image_extensions = ['jpg', 'png', 'jpeg']
            url = None
            
            # 尋找對應的圖片檔案
            for ext in image_extensions:
                image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                if os.path.exists(image_path):
                    image_path = image_path.replace("\\", "/")
                    url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                    break
            
            if not url:
                url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
            
            # 使用 ImageMessage 回傳菜單圖片
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[ImageMessage(original_content_url=url, preview_image_url=url)]
                )
            )
        elif data.startswith("delete_order_"):
            group_order_id = data.replace("delete_order_", "")
            if db_manager.delete_user_order(group_order_id, user_id):
                reply_text = "您的訂單已成功刪除！"
            else:
                reply_text = "刪除訂單失敗，可能訂單已不存在。"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)]
                )
            )
        elif data.startswith("set_time_"):
            group_order_id = data.replace("set_time_", "")
            selected_time = event.postback.params.get('time')  # 獲取用戶選擇的時間
            
            if selected_time:
                # 解析選擇的時間
                hour, minute = map(int, selected_time.split(':'))
                now = datetime.now(UTC)
                close_time = now.replace(hour=(hour-8), minute=minute)  # 調整為 UTC 時間
                
                # 如果選擇的時間比現在早，就設定為明天的同一時間
                if close_time <= now:
                    close_time = close_time + timedelta(days=1)
                
                # 從 Redis 獲取團購資訊
                active_orders = db_manager.get_active_orders()
                order = next((order for order in active_orders if order['id'] == group_order_id), None)
                restaurant = order['restaurant'] if order else "此團購"
                
                if db_manager.set_group_order_close_time(group_order_id, close_time):
                    local_time = (close_time + timedelta(hours=8)).strftime("%H:%M")  # 轉換為台灣時間
                    reply_text = f"{restaurant} 團購已開啟，將於{'今天' if close_time.date() == now.date() else '明天'} {local_time} 自動閉團！\n請輸入「目前團購」選擇團購並開始點餐！"
                else:
                    reply_text = "設定閉團時間失敗，團購可能已不存在。"
                    
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )

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

def get_user_name(user_id):
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(user_id)
            return profile.display_name
    except Exception as e:
        print(f"無法獲取用戶資料: {e}")
        return f"用戶 {user_id[:5]}..."
    
def initialize_redis_and_db():
    try:
        # 確保資料庫表格存在
        with app.app_context():
            db.create_all()
            
        # 清除所有相關的 Redis 資料
        group_keys = redis_client.keys('group_order:*')
        user_keys = redis_client.keys('user:*')
        
        # 如果有找到鍵值才執行刪除
        if group_keys:
            redis_client.delete(*group_keys)
        if user_keys:
            redis_client.delete(*user_keys)
        
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
                        f'user_order:{order.id}:{user_order.user_id}',
                        'items',
                        str(user_order.items)  # 確保轉換為字串
                    )
                    
    except Exception as e:
        print(f"初始化資料庫時發生錯誤: {e}")

# 定時檢查並關閉到期的團購
def check_and_close_orders():
    print("檢查是否有需要自動閉團的團購...")
    closed_orders = db_manager.check_and_close_expired_orders()
    if closed_orders:
        print(f"已自動關閉 {len(closed_orders)} 個團購")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    
    # 檢查並設置 Rich Menu
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            default_rich_menu_id = line_bot_api.get_default_rich_menu_id()
            if not default_rich_menu_id:
                create_rich_menu()
    except Exception as e:
        print(f"檢查 rich menu 時發生錯誤: {e}")
        create_rich_menu()
    
    initialize_redis_and_db()  # 啟動時初始化兩個資料庫
    
    # 設置定時任務，每分鐘檢查一次是否有需要自動閉團的團購
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_close_orders, 'interval', minutes=1)
    scheduler.start()
    
    app.run(debug=True)