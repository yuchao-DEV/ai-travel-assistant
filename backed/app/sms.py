"""短信发送抽象层。

业务代码只依赖 :func:`get_sms_provider`，切换短信通道不需要改动任何业务逻辑。

当前实现：
- ``DevSmsProvider``（默认）—— 开发/模拟模式，验证码只写后端日志，不产生任何费用；
- ``AliyunSmsProvider`` —— 阿里云短信占位实现，接入时只改本文件。

接入真实短信的步骤：在对应 provider 的 ``send()`` 里调用云厂商 SDK（用 httpx 直接打
HTTP 接口也可以，本项目已有 httpx 依赖），然后把 ``SMS_PROVIDER`` 改成对应名字。
"""
import logging
from abc import ABC, abstractmethod

from app.config import SMS_DEBUG_ECHO, SMS_PROVIDER

logger = logging.getLogger("app.sms")


class SmsProvider(ABC):
    """短信通道接口。"""

    name = "base"

    @abstractmethod
    async def send(self, phone: str, code: str, scene: str) -> bool:
        """下发验证码，返回是否成功。"""

    @property
    def echoes_code(self) -> bool:
        """是否允许把验证码回显到接口响应里（仅开发模式为 True）。"""
        return False


class DevSmsProvider(SmsProvider):
    """开发/模拟模式：不真发短信，验证码写日志，可选回显给前端方便联调。"""

    name = "dev"

    @property
    def echoes_code(self) -> bool:
        return SMS_DEBUG_ECHO

    async def send(self, phone: str, code: str, scene: str) -> bool:
        # 只打印手机号后四位以外的部分不敏感信息；验证码本身必须可见否则无法联调
        logger.warning(
            "[SMS-DEV] 模拟发送 场景=%s 手机号=%s 验证码=%s（未真实下发，仅本地调试）",
            scene,
            phone,
            code,
        )
        return True


class AliyunSmsProvider(SmsProvider):
    """阿里云短信（占位）。

    接入需要：AccessKeyId / AccessKeySecret、已审核通过的短信签名、模板 CODE
    （模板形如 ``您的验证码为${code}，5分钟内有效``）。
    """

    name = "aliyun"

    async def send(self, phone: str, code: str, scene: str) -> bool:
        raise NotImplementedError(
            "阿里云短信通道尚未接入。请在 app/sms.py 的 AliyunSmsProvider.send() "
            "中实现真实下发逻辑，并将 .env 的 SMS_DEBUG_ECHO 设为 false。"
        )


class TencentSmsProvider(SmsProvider):
    """腾讯云短信（占位）。接入需要 SecretId/SecretKey、SdkAppId、签名与模板 ID。"""

    name = "tencent"

    async def send(self, phone: str, code: str, scene: str) -> bool:
        raise NotImplementedError(
            "腾讯云短信通道尚未接入。请在 app/sms.py 的 TencentSmsProvider.send() "
            "中实现真实下发逻辑，并将 .env 的 SMS_DEBUG_ECHO 设为 false。"
        )


_PROVIDERS = {
    "dev": DevSmsProvider,
    "aliyun": AliyunSmsProvider,
    "tencent": TencentSmsProvider,
}

_provider: SmsProvider | None = None


def get_sms_provider() -> SmsProvider:
    """取当前配置的短信通道实例（进程内单例）。"""
    global _provider
    if _provider is None:
        provider_cls = _PROVIDERS.get(SMS_PROVIDER)
        if provider_cls is None:
            logger.warning("未知 SMS_PROVIDER=%s，回退到 dev 模拟模式", SMS_PROVIDER)
            provider_cls = DevSmsProvider
        _provider = provider_cls()
        logger.info("短信通道初始化：%s", _provider.name)
    return _provider
