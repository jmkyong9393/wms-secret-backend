"""
네트워크 라벨 프린터(Xprinter XP-423B 등) Raw TCP 전송 서비스.
LABEL_PRINTER_ENABLED=false(기본)이면 실제 연결 없이 건너뛰므로 프린터가 없는 개발 환경에서도 입고·검수 흐름이 깨지지 않는다.
"""
import logging
import socket
from dataclasses import dataclass

from app.core.config import settings


logger = logging.getLogger(__name__)


class LabelPrinterError(RuntimeError):
    """네트워크 라벨 프린터 설정 또는 통신 오류다."""


@dataclass(frozen=True)
class LabelPrintResult:
    """
    ZPL 전송 결과다.
    Raw TCP 프린터는 일반적으로 인쇄 완료 응답을 주지 않으므로, sent=True는 프린터 소켓으로 ZPL 바이트 전송을 완료했다는 뜻이다.
    """

    sent: bool
    skipped: bool
    bytes_sent: int


def send_zpl_to_label_printer(zpl: str) -> LabelPrintResult:
    """
    ZPL 문자열을 네트워크 라벨 프린터의 Raw TCP 포트로 전송한다.
    개발 환경에서 LABEL_PRINTER_ENABLED=false이면 실제 연결 없이 전송을 건너뜀으로써 입고·검수 테스트를 안전하게 지원한다.
    """
    if not zpl.strip():
        raise ValueError("ZPL content must not be empty")

    if not settings.LABEL_PRINTER_ENABLED:
        logger.info("Label printer is disabled. ZPL sending skipped.")
        return LabelPrintResult(sent=False, skipped=True, bytes_sent=0)

    if not settings.LABEL_PRINTER_HOST.strip():
        raise LabelPrinterError(
            "LABEL_PRINTER_HOST is required when LABEL_PRINTER_ENABLED is true"
        )

    try:
        payload = zpl.encode(settings.LABEL_PRINTER_ENCODING)
    except UnicodeEncodeError as exc:
        raise LabelPrinterError(
            "ZPL cannot be encoded with the configured LABEL_PRINTER_ENCODING"
        ) from exc

    try:
        with socket.create_connection(
            (settings.LABEL_PRINTER_HOST, settings.LABEL_PRINTER_PORT),
            timeout=settings.LABEL_PRINTER_TIMEOUT_SECONDS,
        ) as printer_socket:
            printer_socket.sendall(payload)
    except OSError as exc:
        raise LabelPrinterError(
            "Failed to send ZPL to label printer. "
            f"host={settings.LABEL_PRINTER_HOST} "
            f"port={settings.LABEL_PRINTER_PORT}"
        ) from exc

    logger.info("ZPL sent to label printer. bytes_sent=%s", len(payload))

    return LabelPrintResult(sent=True, skipped=False, bytes_sent=len(payload))
