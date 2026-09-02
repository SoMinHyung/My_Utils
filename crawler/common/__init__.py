# crawler.common — 크롤러 프로젝트들이 공유하는 코드.
#
# 주의: 이 __init__ 은 의도적으로 비어 있다.
# kofia_notice_bot 은 Python 3.7 에서 돌기 때문에, 여기서 ticket.py(3.10+ 문법 사용)를
# import 하면 kofia 의 `from common.notifier import ...` 가 깨진다.
# 따라서 서브모듈은 각자 필요할 때만 직접 import 한다.
