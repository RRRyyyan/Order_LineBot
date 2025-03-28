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
from redis import Redis

# 導入 Config 文件中的配置
from config import get_config, Config, OrderConfig, LineBotConfig
from database import db, DatabaseManager

# 初始化 Flask 應用
app = Flask(__name__, static_folder='static')

# 獲取環境配置 (預設為 development)
env_config = get_config('development' if app.debug else 'production')

# 配置 SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = env_config.SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# 獲取redis配置
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
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        restaurant = text.replace("開團", "").strip()
        if not restaurant:
            reply_text = "請輸入餐廳名稱，例如：50嵐開團"
        elif restaurant not in env_config.RESTAURANTS:
            reply_text = f"目前不支援 {restaurant}，可用餐廳：{', '.join(env_config.RESTAURANTS)}"
        else:
            # 檢查是否已有進行中的團購
            active_orders = db_manager.get_active_orders()
            existing_order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
            
            if existing_order:
                leader_id = existing_order['leader_id']
                leader_name = user_names.get(leader_id, f"用戶 {leader_id[:5]}...")
                reply_text = f"{restaurant} 團購已經開啟，開團者: {leader_name}，請先閉團再開新團！"
            else:
                # 創建新的團購
                db_manager.create_group_order(restaurant, user_id)
                reply_text = f"{restaurant} 團購已開啟，請輸入「目前團購」選擇團購並開始點餐！"
        
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)],
            )
        )

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
                            
                            reply_text = summary
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
                    leader_name = user_names.get(leader_id, f"用戶 {leader_id[:5]}...")
                    
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
                        text=f"開團者: {leader_name}",
                        actions=[PostbackAction(label="選擇此團購", data=f"select_group_{order['id']}")]
                    )
                    columns.append(column)

                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateMessage(alt_text="選擇要加入的團購", template=carousel_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
                )
        elif text == "我要點":
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
                    leader_name = user_names.get(leader_id, f"用戶 {leader_id[:5]}...")
                    
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
                        text=f"開團者: {leader_name}",
                        actions=[PostbackAction(label="點選此團購", data=f"order_group_{order['id']}")]
                    )
                    columns.append(column)

                carousel_template = CarouselTemplate(columns=columns)
                template_message = TemplateMessage(alt_text="選擇要點餐的團購", template=carousel_template)
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
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
                            text=items_text,
                            actions=[PostbackAction(label="修改訂單", data=f"edit_order_{restaurant}")]
                        )
                        columns.append(column)

                    carousel_template = CarouselTemplate(columns=columns)
                    template_message = TemplateMessage(alt_text="您的訂單", template=carousel_template)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(reply_token=event.reply_token, messages=[template_message])
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
                        
                        reply_text = summary
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
                reply_text = f"您已選擇 {order['restaurant']} 的團購，請輸入「我要點」開始點餐！"
            else:
                reply_text = "此團購已不存在！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data.startswith("order_group_"):
            group_order_id = data.replace("order_group_", "")
            active_orders = db_manager.get_active_orders()
            order = next((order for order in active_orders if order['id'] == group_order_id), None)
            if order:
                # 儲存用戶選擇的團購
                redis_client.set(f'user:{user_id}:selected_group', group_order_id)
                reply_text = f"您已選擇 {order['restaurant']} 的團購，請開始點餐！"
            else:
                reply_text = "此團購已不存在！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        elif data.startswith("edit_order_"):
            restaurant = data.replace("edit_order_", "")
            active_orders = db_manager.get_active_orders()
            order = next((order for order in active_orders if order['restaurant'] == restaurant), None)
            if order:
                # 儲存用戶選擇的團購
                redis_client.set(f'user:{user_id}:selected_group', order['id'])
                reply_text = f"您已選擇 {restaurant} 的團購，請重新點餐！"
            else:
                reply_text = "此團購已不存在！"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)