# -*- coding: utf-8 -*-
"""공용 텔레그램 알림기.

모든 크롤러 프로젝트가 같은 봇 토큰/chat_id 를 쓰므로, 자격증명은 crawler/secrets.ini
한 곳에 두고 여기서 읽는다. Python 3.7(kofia) 과 3.12(모니터) 모두에서 import 되므로
3.7 문법을 유지한다(런타임 제네릭/유니온 미사용).
"""
from __future__ import annotations

import configparser
import logging
import os

import requests

logger = logging.getLogger(__name__)

# crawler/secrets.ini (이 파일 기준 상위 디렉터리). gitignore 대상.
DEFAULT_SECRETS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "secrets.ini")
)

HTTP_TIMEOUT = 20


class TelegramNotifier:
    """crawler/secrets.ini 의 [Telegram] 에서 bot/chat_id 를 읽어 메시지를 보낸다.

    - secrets_path 로 다른 설정 파일을 지정하거나
    - bot_token/chat_id 를 직접 넘겨서 override 할 수 있다.
    """

    def __init__(self, bot_token=None, chat_id=None, secrets_path=None):
        if bot_token and chat_id:
            self.bot_token = bot_token
            self.chat_id = str(chat_id)
        else:
            path = secrets_path or DEFAULT_SECRETS_PATH
            config = configparser.ConfigParser()
            if not config.read(path, encoding="utf-8"):
                raise RuntimeError(
                    "텔레그램 설정 파일을 찾을 수 없습니다: %s "
                    "(secrets.ini.example 를 복사해 만드세요)" % path
                )
            self.bot_token = config["Telegram"]["bot"]
            self.chat_id = config["Telegram"]["chat_id"]
        self.api_url = "https://api.telegram.org/bot%s/sendMessage" % self.bot_token

    def send(self, text, disable_preview=False):
        """메시지 전송. 성공 True / 실패 False(로그만 남김).

        반환값(bool)은 kofia 처럼 '전송 성공했을 때만 상태를 기록'하는 쪽에서 사용한다.
        """
        try:
            resp = requests.post(
                self.api_url,
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": "true" if disable_preview else "false",
                },
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info("텔레그램 메시지 발송 완료 (%d자)", len(text))
                return True
            logger.error("텔레그램 발송 실패 (%s): %s", resp.status_code, resp.text[:300])
            return False
        except Exception as exc:  # 네트워크/JSON 등 모든 오류
            logger.error("텔레그램 메시지 발송 실패: %s", exc)
            return False
