import os
from dotenv import load_dotenv


# 加載 .env 文件
load_dotenv()
class Config:
    # LINE Bot API 設定
    CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
    CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
    # 餐廳相關設定
    RESTAURANTS = ["50嵐", "八曜和茶", "迷客夏", "mateas", "大茗"]
   
    # 菜單圖片對應
    MENU_DICT = {
        "50嵐": "50lan",
        "八曜和茶": "8yao",
        "迷客夏": "milkshop",
        "mateas": "mateas",
        "大茗": "damin"
    }
        # Rich Menu 設定
    RICH_MENU_SIZE = {
        "width": 2500,
        "height": 843
    }
        # 靜態檔案設定
    STATIC_FOLDER = "static"
    RICH_MENU_IMAGE = "richmenu.png"
    LOGO_IMAGE = "Logo.jpg"

    ## PostgreSQl與redis設置
    SQLALCHEMY_DATABASE_URI = "postgresql://postgres:ryan0404@localhost:5432/line_bot_db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = "redis://localhost:6379/0"
    # 定時任務設置
    SCHEDULER_INTERVAL_MINUTES = 5  # 每 5 分鐘檢查一次過期團購

class DevelopmentConfig(Config):
   DEBUG = True
   # 開發環境特定的設定
class ProductionConfig(Config):
   DEBUG = False
   # 生產環境特定的設定
# 根據環境選擇配置
config = {
   'development': DevelopmentConfig,
   'production': ProductionConfig,
   'default': DevelopmentConfig
   }

# 設定預設配置
def get_config(env='default'):
    return config[env]

# 訂單相關設定
class OrderConfig:
    # 訂單狀態
    ORDER_STATUS = {
        'OPEN': 'open',
        'CLOSED': 'closed'
    }
    
    # 訂單資料結構
    ORDER_STRUCTURE = {
        'status': str,  # open/closed
        'leader': str,  # user_id
        'orders': dict  # {user_id: [items]}
    }

# Line Bot相關設定 
class LineBotConfig:
    # 訊息類型
    MESSAGE_TYPES = {
        'TEXT': 'text',
        'IMAGE': 'image',
        'TEMPLATE': 'template'
    }
    
    # Rich Menu設定
    RICH_MENU_AREAS = [
        {
            'bounds': {'x': 0, 'y': 0, 'width': 836, 'height': 1239},
            'action': '目前團購'
        },
        {
            'bounds': {'x': 845, 'y': 9, 'width': 819, 'height': 1230},
            'action': '我的訂單'  
        },
        {
            'bounds': {'x': 1689, 'y': 0, 'width': 807, 'height': 1233},
            'action': '開團'
        }
    ]
