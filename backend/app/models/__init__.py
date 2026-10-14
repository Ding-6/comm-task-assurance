"""DEPRECATED —— ORM 已迁移到 app.db（PyMySQL 原生 SQL）。

保留此目录仅为兼容老 import 路径，请改用：
  - app.db.query / execute / transaction
  - app.db.row.<Table>Row
  - app.db.repo.{Task,Link,Demand,Alarm,TaskLink,Approval,Plan,Repair,Event}Repo
"""
