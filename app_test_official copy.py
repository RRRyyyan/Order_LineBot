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
    QuickReplyItem
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

# 導入 Config 文件中的配置
from config import get_config, Config, OrderConfig, LineBotConfig  # 假設 config 文件名為 config.py

# 初始化 Flask 應用
app = Flask(__name__, static_folder='static')

# 獲取環境配置 (預設為 development)
env_config = get_config('development' if app.debug else 'production')

# LINE Bot 配置
configuration = Configuration(
    access_token=env_config.CHANNEL_ACCESS_TOKEN
)
line_handler = WebhookHandler(env_config.CHANNEL_SECRET)

# 儲存群組狀態與團購資料
all_orders = {}  # {restaurant: {"status": "open"/"closed", "leader": user_id}}
orders = {}      # {restaurant: {user_id: [meal_items]}}
user_names = {}  # 快取用戶名稱
user_selection = {}  # 儲存用戶當前選擇的團購 {user_id: restaurant}

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
    功能:
    - 檢查使用者輸入的餐廳名稱是否有效。
    - 如果餐廳名稱無效，回覆錯誤訊息。
    - 如果餐廳名稱有效且尚未開團，開啟新的團購並設置團購狀態和團購領導者。
    - 如果餐廳名稱有效但已經開團，回覆已經開團的訊息並提供開團者資訊。
    - 回覆相應的訊息給使用者。
    回覆訊息:
    - 錯誤訊息或成功開團的訊息，根據不同情況回覆不同的訊息。
    """

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        restaurant = text.replace("開團", "").strip()
        if not restaurant:
            reply_text = "請輸入餐廳名稱，例如：50嵐開團"
        elif restaurant not in env_config.RESTAURANTS:
            reply_text = f"目前不支援 {restaurant}，可用餐廳：{', '.join(env_config.RESTAURANTS)}"
        elif restaurant in all_orders and all_orders[restaurant]["status"] == OrderConfig.ORDER_STATUS['OPEN']:
            leader_id = all_orders[restaurant]["leader"]
            leader_name = user_names.get(leader_id, f"用戶 {leader_id[:5]}...")
            reply_text = f"{restaurant} 團購已經開啟，開團者: {leader_name}，請先閉團再開新團！"
        else:
            all_orders[restaurant] = {
                "status": OrderConfig.ORDER_STATUS['OPEN'],
                "leader": user_id,
            }
            orders[restaurant] = {}
            reply_text = f"{restaurant} 團購已開啟，請輸入「目前團購」選擇團購並開始點餐！"
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )

@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    處理接收到的訊息事件。
    參數:
    event (Event): LINE Bot SDK 提供的事件物件，包含訊息內容和發送者資訊。
    功能:
    - 根據訊息內容判斷並執行相應的操作：
        - "開團": 呼叫 handle_open_group 函數處理開團邏輯。
        - "閉團": 處理閉團邏輯，檢查餐廳名稱和團購狀態，並生成訂單總結。
        - "目前團購": 回應目前進行中的團購資訊，生成 CarouselTemplate 顯示各團購選項。
        - "我要點": 處理用戶點餐邏輯，記錄用戶點餐資訊。
        - "我的訂單": 回應用戶在選定團購中的訂單詳情。
    注意:
    - 使用 LINE Bot SDK 的 MessagingApi 進行訊息回應。
    - 使用 Counter 計算訂單中各餐點的數量。
    - 使用 CarouselTemplate 和 TemplateMessage 生成圖文選單回應。
    """
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id 
        text = event.message.text

        if text == "開團":  # 修改這裡，當收到純文字"開團"時
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
                        # 使用正斜線替換反斜線
                        image_path = image_path.replace("\\", "/")
                        # url = f"{request.url_root}{image_path}".replace("http://", "https://")
                        url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                        break
                
                # 如果沒有找到圖片，使用預設圖片
                if not url:
                    url = f"{request.url_root}{env_config.STATIC_FOLDER}/default.png".replace("http://", "https://")
                
                column = CarouselColumn(
                    thumbnail_image_url=url,
                    title=restaurant,
                    text="點選開始開團",
                    actions=[MessageAction(label="開始開團", text=f"{restaurant}開團")]  # 使用 MessageAction
                    # actions=[PostbackAction(label="開始開團", data=f"start_group_{restaurant}", text=f"{restaurant}開團")]
                )
                columns.append(column)
            
            carousel_template = CarouselTemplate(columns=columns)
            template_message = TemplateMessage(alt_text="選擇要開團的店家", template=carousel_template)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
            )
        elif text.endswith("開團"):  # 原有的開團處理邏輯保持不變
            handle_open_group(event, text, user_id)
        elif text == "閉團":
            # 顯示用戶已開的團購
            user_groups = {r: info for r, info in all_orders.items() if info["leader"] == user_id and info["status"] == OrderConfig.ORDER_STATUS['OPEN']}
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
                for restaurant in user_groups.keys():
                    # 支援的圖片格式
                    image_extensions = ['jpg', 'png', 'jpeg']
                    menu_image = env_config.MENU_DICT.get(restaurant)
                    if not menu_image:
                        continue  # 如果沒有菜單圖片，跳過此餐廳
                    
                    # 找到對應的圖片檔案
                    url = None
                    for ext in image_extensions:
                        image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                        if os.path.exists(image_path):
                            # 使用正斜線替換反斜線
                            image_path = image_path.replace("\\", "/")
                            # url = f"{request.url_root}{image_path}".replace("http://", "https://")
                            url = f"{request.url_root}{image_path}?t={int(time.time())}".replace("http://", "https://")
                            break
                    
                    # 如果沒有找到圖片，使用預設圖片
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
        elif text.endswith("閉團") and text !="閉團":
            restaurant = text.replace("閉團", "").strip()
            if not restaurant or restaurant not in all_orders:
                reply_text = "請輸入正確的餐廳名稱，例如：50嵐閉團"
            elif all_orders[restaurant]["status"] == OrderConfig.ORDER_STATUS['CLOSED']:
                reply_text = f"{restaurant} 團購已經關閉！"
            elif all_orders[restaurant]["leader"] != user_id:
                reply_text = "只有開團者可以閉團！"
            else:
                # 統計訂單資訊
                summary = generate_order_summary(restaurant)
                all_orders[restaurant]["status"] = OrderConfig.ORDER_STATUS['CLOSED']
                reply_text = f"{restaurant} 團購已關閉！\n\n{summary}"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        
        elif text == "目前團購":
            open_groups = {r: info for r, info in all_orders.items() if info["status"] == OrderConfig.ORDER_STATUS['OPEN']}
            if not open_groups:
                reply_text = "目前沒有進行中的團購，請先開團！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
            else:
                columns = []
                for restaurant in open_groups.keys():
                    order_count = len(orders.get(restaurant, {}))
                    ## 支援的圖片格式
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
                    # url = f"{request.url_root}{env_config.STATIC_FOLDER}/{menu_image}.jpg".replace("http", "https")
                    # 找到對應的圖片檔案
                    for ext in image_extensions:
                        image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
                        if os.path.exists(image_path):
                            # 使用正斜線替換反斜線
                            image_path = image_path.replace("\\", "/")
                            url = f"{request.url_root}{image_path}".replace("http", "https")
                            break
                    else:
                        url = None  # 如果沒有找到對應的圖片，設為 None 或其他處理方式
                    # url = f"{request.url_root}{env_config.STATIC_FOLDER}/{env_config.LOGO_IMAGE}".replace("http", "https")
                    column = CarouselColumn(
                        thumbnail_image_url=url,
                        title=restaurant,
                        text=f"開團中 - 已有{order_count}人點餐",
                        actions=[
                            PostbackAction(label="點餐", data=f"order_{restaurant}", text=f"開始在{restaurant}點餐"),
                            PostbackAction(label="修改訂單", data=f"modify_{restaurant}", text=f"修改我在{restaurant}的訂單"),
                            PostbackAction(label="菜單價目表", data=f"menu_{restaurant}", text=f"查看{restaurant}的菜單"),
                        ],
                    )
                    columns.append(column)
                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateMessage(alt_text="選擇團購", template=carousel_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                )
        elif text.startswith("我要點"):
            if user_id not in user_selection or user_selection[user_id] not in all_orders:
                reply_text = "請先輸入「目前團購」選擇團購！"
            elif all_orders[user_selection[user_id]]["status"] != OrderConfig.ORDER_STATUS['OPEN']:
                reply_text = f"{user_selection[user_id]} 團購已關閉！"
            else:
                restaurant = user_selection[user_id]
                meal_text = text.replace("我要點", "").strip()
                if not meal_text:
                    reply_text = "請輸入餐點名稱，例如：我要點 珍珠奶茶"
                else:
                    meals = [meal.strip() for meal in re.split(r"[ ;,、]", meal_text) if meal.strip()]
                    if user_id not in orders[restaurant]:
                        orders[restaurant][user_id] = []
                    orders[restaurant][user_id].extend(meals)
                    reply_text = f"已記錄 {', '.join(meals)} 到 {restaurant} 團購，你的點餐：{orders[restaurant][user_id]}"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
        # elif text == "我的訂單":
        #     if user_id not in user_selection or user_selection[user_id] not in all_orders:
        #         reply_text = "請先輸入「目前團購」選擇團購！"
        #     else:
        #         restaurant = user_selection[user_id]
        #         if restaurant not in orders or user_id not in orders[restaurant] or not orders[restaurant][user_id]:
        #             reply_text = f"你在 {restaurant} 團購中還沒有點餐！"
        #         else:
        #             ordered_items = Counter(orders[restaurant][user_id])
        #             items_str = ", ".join(f"{item}*{count}" for item, count in ordered_items.items())
        #             reply_text = f"你在 {restaurant} 的訂單：\n{items_str}"
        #     line_bot_api.reply_message(
        #         ReplyMessageRequest(
        #             reply_token=event.reply_token,
        #             messages=[TextMessage(text=reply_text)],
        #         )
        #     )
        elif text == "我的訂單":
            user_has_orders = False
            all_user_orders = []
            
            # 遍歷所有餐廳，檢查用戶是否有訂單
            for restaurant_name, restaurant_orders in orders.items():
                if user_id in restaurant_orders and restaurant_orders[user_id]:
                    user_has_orders = True
                    ordered_items = Counter(restaurant_orders[user_id])
                    items_str = ", ".join(f"{item}*{count}" for item, count in ordered_items.items())
                    all_user_orders.append(f"【{restaurant_name}】\n{items_str}")
            
            if user_has_orders:
                # 用戶有訂單，顯示所有訂單信息
                reply_text = "你的所有訂單：\n" + "\n\n".join(all_user_orders)
            else:
                # 用戶沒有任何訂單
                reply_text = "你目前還沒有點餐喔!\n請點選目前團購進行點餐"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

def generate_order_summary(restaurant):
    """
    統計訂單資訊的函數。
    參數:
    restaurant (str): 餐廳名稱。
    返回:
    str: 訂單統計的摘要資訊。
    """
    if restaurant not in orders or not orders[restaurant]:
        return "沒有任何訂單。"

    all_meals = [meal for user_meals in orders[restaurant].values() for meal in user_meals]
    meal_counts = Counter(all_meals)
    summary = f"{restaurant} 團購已結束，訂單統整如下：\n\n"
    summary += "各品項總數：\n" + "\n".join(f"{meal}: {count}" for meal, count in meal_counts.items()) + "\n\n"
    summary += "個人訂單：\n"
    
    for uid, meals in orders[restaurant].items():
        if uid not in user_names:
            try:
                profile = line_bot_api.get_profile(user_id=uid)
                user_names[uid] = profile.display_name
            except Exception:
                user_names[uid] = f"用戶 {uid[:5]}..."
        personal_orders = Counter(meals)
        user_name = user_names[uid]
        personal_orders_str = ", ".join(f"{meal}*{count}" for meal, count in personal_orders.items())
        summary += f"{user_name}: {personal_orders_str}\n"
    
    return summary


@line_handler.add(PostbackEvent)
def handle_postback(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        data = event.postback.data
        user_id = event.source.user_id

        def handle_order(event, restaurant):
            """
            處理團購選擇的函數。

            這個函數會根據用戶選擇的餐廳，更新用戶的選擇紀錄，並回傳相關的訊息，讓用戶開始點餐。

            Args:
                event (PostbackEvent): LINE Bot收到的回傳事件。
                restaurant (str): 用戶選擇的餐廳名稱。
            """
            user_selection[user_id] = restaurant
            reply_text = f"你已選擇 {restaurant} 團購，請輸入「我要點 [餐點(備註)]」開始點餐！"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

        def handle_modify(event, restaurant):
            """
            處理團購修改的函數。

            這個函數會根據用戶選擇的餐廳，更新用戶的選擇紀錄，並回傳相關的訊息，讓用戶能夠修改或開始點餐。

            Args:
                event (PostbackEvent): LINE Bot收到的回傳事件。
                restaurant (str): 用戶選擇的餐廳名稱。
            """
            user_selection[user_id] = restaurant
            if restaurant in orders and user_id in orders[restaurant] and orders[restaurant][user_id]:
                ordered_items = Counter(orders[restaurant][user_id])
                items_str = ", ".join(f"{item}*{count}" for item, count in ordered_items.items())
                reply_text = f"你在 {restaurant} 的當前訂單：\n{items_str}\n\n請輸入「我要點 餐點(備註)」來替換你的訂單。"
            else:
                reply_text = f"你在 {restaurant} 還沒有點餐，請輸入「我要點 餐點(備註)」開始點餐！"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        
        # def handle_menu(event, restaurant):
        #     """
        #     處理菜單選擇的函數。

        #     這個函數會根據用戶選擇的餐廳，回傳相關的菜單訊息，讓用戶能夠查看菜單。

        #     Args:
        #         event (PostbackEvent): LINE Bot收到的回傳事件。
        #         restaurant (str): 用戶選擇的餐廳名稱。
        #     """
        #     user_selection[user_id] = restaurant
        #     # 支援的圖片格式
        #     image_extensions = ['jpg', 'png', 'jpeg']
        #     menu_image = env_config.MENU_DICT.get(restaurant)
        #     if not menu_image:
        #         reply_text = "目前無此餐廳的菜單資訊！"
        #         line_bot_api.reply_message(
        #             ReplyMessageRequest(
        #                 reply_token=event.reply_token,
        #                 messages=[TextMessage(text=reply_text)],
        #             )
        #         )
        #         return
        #     # url = f"{request.url_root}{env_config.STATIC_FOLDER}/{menu_image}.jpg".replace("http", "https")
        #     # 找到對應的圖片檔案
        #     for ext in image_extensions:
        #         image_path = f"{env_config.STATIC_FOLDER}/store_images/{menu_image}.{ext}"
        #         if os.path.exists(os.path.join(request.url_root, image_path)):
        #             url = f"{request.url_root}{image_path}".replace("http", "https")
        #             break
        #     else:
        #         url = None  # 如果沒有找到對應的圖片，設為 None 或其他處理方式
        #     line_bot_api.reply_message(
        #         ReplyMessageRequest(
        #             reply_token=event.reply_token,
        #             messages=[ImageMessage(original_content_url=url, preview_image_url=url)],
        #         )
        #     )
        
        def handle_menu(event, restaurant):
            """
            處理菜單選擇的函數。

            這個函數會根據用戶選擇的餐廳，直接回覆一個連結，讓LINE應用開啟該餐廳的菜單URL。

            Args:
                event (PostbackEvent): LINE Bot收到的回傳事件。
                restaurant (str): 用戶選擇的餐廳名稱。
            """
            user_selection[user_id] = restaurant
            
            # 檢查餐廳是否存在
            menu_url = "https://order.nidin.shop/"  # 替換為實際的菜單 URL
            if not menu_url:
                reply_text = "目前無此餐廳的菜單資訊！"
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)],
                    )
                )
                return
            
            # 使用 ButtonsTemplate 來顯示按鈕
            buttons_template = ButtonsTemplate(
                title=f"{restaurant} 菜單",
                text="點擊下方按鈕查看菜單",
                actions=[
                    URIAction(label="去訂餐", uri=menu_url)  # 使用 URIAction 直接開啟 URL
                ]
            )
            
            template_message = TemplateMessage(alt_text="去訂餐", template=buttons_template)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[template_message]
                )
            )

        def open_shop(event, restaurant):
            reply_text = f"【{restaurant}】開團"
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

        def close_group(event, restaurant):
            close_shop = ""
            if restaurant in all_orders and all_orders[restaurant]["leader"] == user_id:
                close_shop = f"{restaurant}"
                # 統計訂單資訊
                summary = generate_order_summary(restaurant)
                all_orders[restaurant]["status"] = OrderConfig.ORDER_STATUS['CLOSED']
                reply_text = f"{restaurant} 團購已成功關閉！\n\n{summary}"
            else:
                reply_text = "你無法關閉此團購！"
            
            # 建立多則訊息包含文字訊息及按鈕模板
            messages = [
                TextMessage(text=reply_text),
                TemplateMessage(
                    alt_text="去訂餐囉", 
                    template=ButtonsTemplate(
                        title=f"{close_shop} 團購已關閉",
                        text="去訂餐囉",
                        actions=[URIAction(label="前往訂餐", uri="https://order.nidin.shop/")]
                    )
                )
            ]
    
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=messages
                )
            )
                
        postback_handlers = {
            "order_": handle_order,
            "modify_": handle_modify,
            "menu_": handle_menu,
            # "start_group_": lambda e, r: handle_open_group(e, f"{r}開團", user_id)
            "start_group_": open_shop,
            "close_group_": close_group  # 新增關閉團購的處理
        }

        for prefix, handler in postback_handlers.items():
            if data.startswith(prefix):
                handler(event, data.replace(prefix, ""))
                break

def create_rich_menu():
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
                        action=MessageAction(text=area['action'])  # 這會直接發送文字訊息
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

if __name__ == "__main__":
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            default_rich_menu_id = line_bot_api.get_default_rich_menu_id()
            if not default_rich_menu_id:
                create_rich_menu()
    except Exception as e:
        print(f"檢查 rich menu 時發生錯誤: {e}")
        create_rich_menu()
    
    app.run(debug=env_config.DEBUG)