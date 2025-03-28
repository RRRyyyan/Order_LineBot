from flask_sqlalchemy import SQLAlchemy  # 引入 Flask-SQLAlchemy 來處理資料庫交互
from redis import Redis  # 引入 Redis 來處理緩存
import json  # 引入 json 來處理 JSON 資料
from datetime import datetime, UTC  # 引入 datetime 來處理日期時間，UTC 來處理時區

db = SQLAlchemy()  # 創建 SQLAlchemy 實例

class GroupOrder(db.Model):  # 定義 GroupOrder 模型
    __tablename__ = 'group_orders'  # 定義資料表名稱
    id = db.Column(db.Integer, primary_key=True)  # 定義 id 欄位為主鍵
    restaurant = db.Column(db.String(100), nullable=False)  # 定義餐廳名稱欄位
    leader_id = db.Column(db.String(100), nullable=False)  # 定義團購領導者 ID 欄位
    status = db.Column(db.String(20), default='open')  # 定義團購狀態欄位，預設為 'open'
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 定義創建時間欄位，預設為 UTC 時區的當前時間
    closed_at = db.Column(db.DateTime)  # 定義關閉時間欄位

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
        group_order = GroupOrder(
            restaurant=restaurant,
            leader_id=leader_id,
            status='open'
        )
        db.session.add(group_order)  # 將團購添加到資料庫會話中
        db.session.commit()  # 提交資料庫變更
        
        # 在 Redis 中設置即時狀態
        redis_key = f'group_order:{group_order.id}'  # 生成 Redis 鍵
        self.redis.hset(redis_key, mapping={
            'restaurant': restaurant,
            'leader_id': leader_id,
            'status': 'open',
            'created_at': str(datetime.now(UTC))
        })  # 使用 Redis 的 Hash 類型存儲團購信息
        return group_order  # 返回創建的團購實例

    def get_active_orders(self):
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
                    if order_data and order_data.get(b'status', b'').decode() == 'open':
                        # 如果訂單是活躍的，將其添加到 active_orders 列表
                        active_orders.append({
                            # 從鍵中提取訂單 ID（假設鍵的格式是 'group_order:ID'）
                            'id': key.decode().split(':')[1],
                            # 解碼並提取餐廳名稱
                            'restaurant': order_data[b'restaurant'].decode(),
                            # 解碼並提取訂單發起人（團長）的 ID
                            'leader_id': order_data[b'leader_id'].decode()
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

    def get_user_orders(self, group_order_id):  # 獲取團購中的所有訂單
        """獲取團購中的所有訂單"""
        redis_key = f'group_order:{group_order_id}:orders'  # 生成 Redis 鍵
        orders = self.redis.hgetall(redis_key)  # 獲取團購中的所有訂單
        return {k.decode(): json.loads(v.decode()) for k, v in orders.items()}  # 返回解析後的訂單字典

    def get_user_order(self, group_order_id, user_id):  # 獲取特定用戶的訂單
        """獲取特定用戶的訂單"""
        redis_key = f'group_order:{group_order_id}:orders'  # 生成 Redis 鍵
        order = self.redis.hget(redis_key, user_id)  # 獲取特定用戶的訂單
        return json.loads(order.decode()) if order else None  # 返回解析後的訂單，或者如果不存在則返回 None