from flask import Flask
from flask_sqlalchemy import SQLAlchemy  # 引入 Flask-SQLAlchemy 來處理資料庫交互
from redis import Redis  # 引入 Redis 來處理緩存
import json  # 引入 json 來處理 JSON 資料
from datetime import datetime, UTC, timedelta,timezone  # 引入 datetime 來處理日期時間，UTC 來處理時區
from config import get_config
db = SQLAlchemy()
# 初始化 Flask 應用
app = Flask(__name__, static_folder='static')

# 獲取環境配置 (預設為 development)
env_config = get_config('development' if app.debug else 'production')

# 配置 SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = env_config.SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)



class GroupOrder(db.Model):  # 定義 GroupOrder 模型
    __tablename__ = 'group_orders'  # 定義資料表名稱
    id = db.Column(db.Integer, primary_key=True)  # 定義 id 欄位為主鍵
    restaurant = db.Column(db.String(100), nullable=False)  # 定義餐廳名稱欄位
    leader_id = db.Column(db.String(100), nullable=False)  # 定義團購領導者 ID 欄位
    status = db.Column(db.String(20), default='open')  # 定義團購狀態欄位，預設為 'open'
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 定義創建時間欄位，預設為 UTC 時區的當前時間
    closed_at = db.Column(db.DateTime)  # 定義關閉時間欄位
    close_time = db.Column(db.DateTime(timezone=True))  # 新增: 預計閉團時間欄位，並指定為 UTC 時間  # 新增: 預計閉團時間欄位

class UserOrder(db.Model):  # 定義 UserOrder 模型
    __tablename__ = 'user_orders'  # 定義資料表名稱
    id = db.Column(db.Integer, primary_key=True)  # 定義 id 欄位為主鍵
    group_order_id = db.Column(db.Integer, db.ForeignKey('group_orders.id'))  # 定義團購 ID 欄位，參考 GroupOrder 模型的 id
    user_id = db.Column(db.String(100), nullable=False)  # 定義用戶 ID 欄位
    items = db.Column(db.JSON)  # 定義訂單項目欄位，使用 JSON 類型
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 定義創建時間欄位，預設為 UTC 時區的當前時間

class DatabaseManager:  # 定義 DatabaseManager 類別
    def __init__(self, redis_client):  # 初始化方法，接收 Redis 客戶端實例
        self.redis = redis_client  # 將 Redis 客戶端實例存儲為類別屬性

    def create_group_order(self, restaurant, leader_id):  # 創建新的團購
        """創建新的團購"""
        try:
            group_order = GroupOrder(
                restaurant=restaurant,
                leader_id=leader_id,
                status='open',
                close_time=datetime.now(UTC) + timedelta(hours=24)  # 預設24小時後關閉
            )
            db.session.add(group_order)
            db.session.commit()
            
            # 在 Redis 中設置即時狀態
            redis_key = f'group_order:{group_order.id}'
            try:
                # 使用 Redis 的 hset 命令將團購資訊儲存為 hash 結構
                # redis_key 格式為 'group_order:{id}'
                self.redis.hset(redis_key, mapping={
                    'restaurant': restaurant,      # 儲存餐廳名稱
                    'leader_id': leader_id,       # 儲存開團者 ID
                    'status': 'open',             # 設定團購狀態為開啟
                    'created_at': str(datetime.now(UTC)),  # 儲存建立時間(UTC)
                    'close_time': group_order.close_time.isoformat() if group_order.close_time else ''  # 儲存預計關閉時間,若無則存空字串
                })
            except Exception as redis_error:
                print(f"Redis 錯誤: {redis_error}")
                # Redis 錯誤不應該影響主要功能，所以只記錄不拋出
                
            return group_order
        except Exception as e:
            print(f"創建團購時發生錯誤: {e}")
            db.session.rollback()  # 回滾資料庫事務
            raise  # 重新拋出異常以便上層處理

    def get_active_orders(self):
        try:
            active_orders = []
            self.redis.ping()

            # 先從 PostgreSQL 獲取活躍訂單
            with app.app_context():
                pg_orders = GroupOrder.query.filter_by(status='open').all()
                
                # 將 PostgreSQL 訂單同步到 Redis
                for order in pg_orders:
                    redis_key = f'group_order:{order.id}'
                    # 每次都更新 Redis 中的資料
                    self.redis.hset(
                        redis_key,
                        mapping={
                            'restaurant': str(order.restaurant),
                            'leader_id': str(order.leader_id),
                            'status': 'open',
                            'close_time': order.close_time.isoformat() if order.close_time else '',
                            'id': str(order.id)
                        }
                    )

                    # 同步該團購的所有用戶訂單
                    user_orders = UserOrder.query.filter_by(group_order_id=order.id).all()
                    for user_order in user_orders:
                        self.redis.hset(
                            f'group_order:{order.id}:orders',
                            user_order.user_id,
                            json.dumps(user_order.items)
                        )

                    # 從 Redis 讀取資料並正確處理編碼
                    order_data = self.redis.hgetall(redis_key)
                    order_dict = {
                        'id': order_data.get("id", '') or '',
                        'restaurant': order_data.get('restaurant', '') or '',
                        'leader_id': order_data.get('leader_id', '') or '',
                        'status': order_data.get('status', '') or '',
                        'close_time': order_data.get('close_time', '') or ''
                    }
                    active_orders.append(order_dict)

            return active_orders

        except Exception as e:
            app.logger.error(f"獲取活躍訂單時發生錯誤: {e}")
            return []
    
    def get_closed_orders(self):
        try:
            # 初始化一個空列表，用於存儲活躍的訂單
            active_orders = []

            # 確保 Redis 連接正常，檢查連接是否可用
            # 如果 Redis 連接出現問題，這裡會拋出異常
            self.redis.ping()

            # 使用 Redis 的 keys 方法查找所有符合 'group_order:*' 模式的鍵
            # 這意味著找出所有團體訂單相關的鍵
            for key in self.redis.keys('group_order:*'):
                try:
                    # 使用 hgetall 獲取特定鍵的所有雜湊值
                    # 這將返回該訂單的所有相關數據
                    order_data = self.redis.hgetall(key)

                    # 檢查訂單數據是否存在，並且狀態是 'open'
                    # 使用 .get() 方法安全地獲取狀態，預設為空字節串
                    # 使用 decode() 將字節串轉換為普通字串
                    if order_data and order_data.get(b'status', b'').decode() == 'closed':
                        # 如果訂單是活躍的，將其添加到 active_orders 列表
                        active_orders.append({
                            # 從鍵中提取訂單 ID（假設鍵的格式是 'group_order:ID'）
                            'id': key.decode().split(':')[1],
                            # 解碼並提取餐廳名稱
                            'restaurant': order_data[b'restaurant'].decode(),
                            # 解碼並提取訂單發起人（團長）的 ID
                            'leader_id': order_data[b'leader_id'].decode(),
                            # 解碼並提取訂單的閉團時間
                            'close_time': order_data[b'close_time'].decode()
                        })
                except Exception as e:
                    # 如果處理單個訂單時出現異常，捕獲並列印錯誤，然後繼續處理下一個訂單
                    print(f"處理訂單資料時發生錯誤: {e}")
                    continue

            # 返回所有活躍的訂單列表
            return active_orders

        except Exception as e:
            # 如果在整個獲取過程中出現任何嚴重錯誤，列印錯誤並返回空列表
            print(f"獲取活躍訂單時發生錯誤: {e}")
            return []

    def close_group_order(self, restaurant, leader_id):  # 關閉團購
        """關閉團購"""
        # 從 Redis 獲取團購 ID
        for key in self.redis.keys('group_order:*'):  # 遍歷所有以 'group_order:' 開頭的 Redis 鍵
            order_data = self.redis.hgetall(key)  # 獲取團購的 Hash 值
            if (order_data[b'restaurant'].decode() == restaurant and 
                order_data[b'leader_id'].decode() == leader_id and 
                order_data[b'status'].decode() == 'open'):  # 檢查團購是否符合條件
                group_order_id = key.decode().split(':')[1]  # 解析團購 ID
                
                # 更新 PostgreSQL
                group_order = GroupOrder.query.get(group_order_id)  # 根據團購 ID 獲取團購實例
                if group_order:
                    group_order.status = 'closed'  # 更新團購狀態為 'closed'
                    group_order.closed_at = datetime.now(UTC)  # 設置關閉時間
                    db.session.commit()  # 提交資料庫變更
                
                # 更新 Redis
                self.redis.hset(key, 'status', 'closed')  # 更新團購狀態為 'closed'
                self.redis.hset(key, 'closed_at', str(datetime.now(UTC)))  # 設置關閉時間
                return True  # 返回成功標誌
        return False  # 如果未找到符合條件的團購，返回失敗標誌

    def add_user_order(self, group_order_id, user_id, items):  # 添加用戶訂單
        """添加用戶訂單"""
        # 保存到 PostgreSQL
        user_order = UserOrder(
            group_order_id=group_order_id,
            user_id=user_id,
            items=items
        )
        db.session.add(user_order)  # 將用戶訂單添加到資料庫會話中
        db.session.commit()  # 提交資料庫變更
        
        # 更新 Redis
        redis_key = f'group_order:{group_order_id}:orders'  # 生成 Redis 鍵
        self.redis.hset(redis_key, user_id, json.dumps(items))  # 使用 Redis 的 Hash 類型存儲用戶訂單

    def get_user_orders(self, group_order_id):
        """獲取團購中的所有訂單"""
        redis_key = f'group_order:{group_order_id}:orders'  # 生成 Redis 鍵
        orders = self.redis.hgetall(redis_key)  # 獲取團購中的所有訂單
        return {k: json.loads(v) for k, v in orders.items()}  # 直接使用字串，不需要 decode

    def get_user_order(self, group_order_id, user_id):
        """獲取特定用戶的訂單"""
        redis_key = f'group_order:{group_order_id}:orders'
        order = self.redis.hget(redis_key, user_id)
        if order:
            if isinstance(order, bytes):
                return json.loads(order.decode())
            else:
                return json.loads(order)
        return None
    
    def delete_user_order(self, group_order_id, user_id):
        """刪除訂單"""
        try:
            # 從 PostgreSQL 刪除所有符合條件的訂單
            user_orders = UserOrder.query.filter_by(
                group_order_id=group_order_id,
                user_id=user_id
            ).all()  # 使用 .all() 取得所有符合的訂單
            
            if user_orders:
                for order in user_orders:
                    db.session.delete(order)
                db.session.commit()
                
                # 修正：使用正確的 Redis 鍵值格式
                redis_key = f'group_order:{group_order_id}:orders'  # 與 get_user_order 使用相同的鍵值格式
                self.redis.hdel(redis_key, user_id)  # 使用 hdel 刪除 hash 中的特定欄位
                return True
            return False
        except Exception as e:
            print(f"刪除訂單時發生錯誤: {e}")
            db.session.rollback()  # 發生錯誤時回滾事務
            return False

    def set_group_order_close_time(self, group_order_id, close_time):
        """設定團購閉團時間"""
        try:
            with app.app_context():
                # 確保 group_order_id 是整數
                group_order_id = int(group_order_id)
                
                # 如果輸入的時間沒有時區信息，假設它是台灣時間
                if close_time.tzinfo is None:
                    tw_tz = timezone(timedelta(hours=8))
                    close_time = close_time.replace(tzinfo=tw_tz)
                
                # 保持時區信息，不要移除
                utc_time = close_time.astimezone(UTC)
                
                # 更新 PostgreSQL，保留時區信息
                group_order = GroupOrder.query.get(group_order_id)
                if group_order:
                    # 直接存儲 UTC 時間，保留時區信息
                    group_order.close_time = utc_time
                    db.session.commit()
                    
                    # 更新 Redis，存儲帶時區的 ISO 格式時間字符串
                    redis_key = f'group_order:{group_order_id}'
                    self.redis.hset(redis_key, 'close_time', utc_time.isoformat())
                    return True
                return False
        except Exception as e:
            print(f"設定閉團時間時發生錯誤: {e}")
            db.session.rollback()
            return False
    
    def check_and_close_expired_orders(self):
        """檢查並關閉已到期的團購"""
        try:
            with app.app_context():  # 確保在應用上下文中執行
                # 從 PostgreSQL 獲取所有開啟的團購
                current_time = datetime.now(UTC)
                orders_to_close = GroupOrder.query.filter(
                    GroupOrder.status == 'open',
                    GroupOrder.close_time <= current_time
                ).all()
                
                closed_orders = []
                
                # 關閉已到期的團購
                for order in orders_to_close:
                    self.close_group_order(order.restaurant, order.leader_id)
                    closed_orders.append({
                        'id': order.id,
                        'restaurant': order.restaurant,
                        'leader_id': order.leader_id
                    })
                
                return closed_orders
        except Exception as e:
            print(f"檢查並關閉到期團購時發生錯誤: {e}")
            return []