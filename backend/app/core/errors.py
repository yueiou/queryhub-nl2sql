"""业务异常：服务层抛出，API 层统一转成 HTTP 响应。"""


class AppError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message="资源不存在"):
        super().__init__(message, 404)
