# models.py
from datetime import datetime, UTC  # 引入datetime類別和UTC
from flask_sqlalchemy import SQLAlchemy  # 引入SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB  # 引入JSONB類別

db = SQLAlchemy()  # 創建SQLAlchemy實例

class User(db.Model):  # 定義User模型
    __tablename__ = 'users'  # 定義表名
    id = db.Column(db.String(100), primary_key=True)  # LINE user ID
    name = db.Column(db.String(100))  # 使用者姓名
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 創建時間

class Restaurant(db.Model):  # 定義Restaurant模型
    __tablename__ = 'restaurants'  # 定義表名
    id = db.Column(db.Integer, primary_key=True)  # 餐廳ID
    name = db.Column(db.String(100), unique=True, nullable=False)  # 餐廳名稱
    menu_image = db.Column(db.String(255))  # 菜單圖片
    is_active = db.Column(db.Boolean, default=True)  # 是否活躍
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 創建時間

class GroupOrder(db.Model):  # 定義GroupOrder模型
    __tablename__ = 'group_orders'  # 定義表名
    id = db.Column(db.Integer, primary_key=True)  # 團體訂單ID
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'), nullable=False)  # 餐廳ID
    creator_id = db.Column(db.String(100), db.ForeignKey('users.id'), nullable=False)  # 創建者ID
    status = db.Column(db.String(20), default='open')  # 訂單狀態
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 創建時間
    closed_at = db.Column(db.DateTime)  # 關閉時間
    
    restaurant = db.relationship('Restaurant', backref='orders')  # 餐廳關聯
    creator = db.relationship('User')  # 創建者關聯

class UserOrder(db.Model):  # 定義UserOrder模型
    __tablename__ = 'user_orders'  # 定義表名
    id = db.Column(db.Integer, primary_key=True)  # 使用者訂單ID
    group_order_id = db.Column(db.Integer, db.ForeignKey('group_orders.id'), nullable=False)  # 團體訂單ID
    user_id = db.Column(db.String(100), db.ForeignKey('users.id'), nullable=False)  # 使用者ID
    items = db.Column(JSONB)  # 訂單項目
    created_at = db.Column(db.DateTime, default=datetime.now(UTC))  # 創建時間
    updated_at = db.Column(db.DateTime, onupdate=datetime.now(UTC))  # 更新時間
    
    group_order = db.relationship('GroupOrder', backref='participants')  # 團體訂單關聯
    user = db.relationship('User')  # 使用者關聯