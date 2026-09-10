"""
네이버 뉴스 검색(엘랑코 브랜드 키워드) → 전일 09:01 ~ 당일 09:00(KST) 사이 게시 기사만 필터링
→ 브랜드 그룹(섹션)별로 묶어서 1통의 이메일로 발송

CBRE 뉴스 알림(cbre_news_alert.py)과 동일한 구조이며, 차이점은 다음과 같습니다.
  - 검색 키워드가 1개가 아니라 "브랜드 그룹"별로 여러 개입니다.
  - 그룹마다 이메일을 따로 보내지 않고, 1통의 메일 안에 그룹별 섹션으로 나눠서 보냅니다.

필요한 환경변수 (GitHub Actions Secrets 로 등록):
  NAVER_CLIENT_ID      네이버 오픈API 애플리케이션 Client ID
  NAVER_CLIENT_SECRET  네이버 오픈API 애플리케이션 Client Secret
  SMTP_HOST            예: smtp.gmail.com
  SMTP_PORT            예: 587
  SMTP_USER            발신 이메일 계정
  SMTP_PASSWORD        발신 이메일 앱 비밀번호(2단계 인증 앱 비밀번호 권장)
  MAIL_FROM            발신자 표시 이메일 (SMTP_USER와 동일해도 무방)
  MAIL_TO              수신자 이메일 (여러 명이면 콤마로 구분)

검색 키워드(브랜드/섹션 구성)를 바꾸고 싶으면 아래 QUERY_GROUPS 딕셔너리만
수정하면 됩니다. key가 메일에 표시되는 섹션 제목이고, value(리스트)가 해당
섹션에서 검색할 키워드들입니다. 같은 섹션 안의 키워드로 찾은 기사는 모두
합쳐서 중복 제거한 뒤 하나의 섹션으로 표시됩니다.

네이버 뉴스 검색 API는 형태소 단위로 매칭하기 때문에, 예를 들어 "넥스가드"를
검색해도 기사 어딘가에 "넥스"와 "가드"가 따로 떨어져 있기만 해도 검색 결과에
걸릴 수 있습니다. 이를 막기 위해 검색 결과로 받은 기사마다 실제로 키워드가
붙어서(연속된 문자열로) 등장하는지 제목/요약 → 기사 본문 순으로 다시 확인하고,
그렇지 않으면(형태소만 분리되어 매칭된 오탐이면) 결과에서 제외합니다.
"""

import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.header import Header
from urllib.parse import urlparse

import requests

# 기자명 조회 시 사용할 User-Agent (차단 방지용)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 브랜드 그룹(섹션) 구성. key = 메일에 표시될 섹션 제목, value = 검색 키워드 목록.
# "심파리카" 섹션은 "심파리카/심패리카" 두 표기를 모두 검색합니다(오탈자·표기
# 차이로 인한 누락 방지 목적).
QUERY_GROUPS = {
    "크레델리오": ["크레델리오", "크레델리오플러스"],
    "넥스가드": ["넥스가드", "넥스가드스펙트라"],
    "심파리카": ["심파리카", "심파리카트리오", "심패리카", "심패리카트리오"],
}

KST = timezone(timedelta(hours=9))

# 도메인 → 한글 언론사명 매핑. 여기 없는 도메인은 도메인 그대로 표시됩니다.
# 새 매체를 추가하고 싶으면 "도메인": "언론사명" 형태로 한 줄 추가하면 됩니다.
PRESS_NAME_MAP = {
    # 종합/경제지
    "chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈",
    "joongang.co.kr": "중앙일보",
    "joins.com": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보",
    "kmib.co.kr": "국민일보",
    "segye.com": "세계일보",
    "munhwa.com": "문화일보",
    "naeil.com": "내일신문",
    "seoul.co.kr": "서울신문",
    "imaeil.com": "매일신문",
    "shinailbo.co.kr": "신아일보",
    "newdaily.co.kr": "뉴데일리",
    "dailian.co.kr": "데일리안",
    "nocutnews.co.kr": "노컷뉴스",
    "kukinews.com": "쿠키뉴스",
    "daily.hankooki.com": "데일리한국",
    "pinpointnews.co.kr": "핀포인트뉴스",
    "hansbiz.co.kr": "한스경제",
    # 경제/증권 전문지
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "fnnews.com": "파이낸셜뉴스",
    "asiae.co.kr": "아시아경제",
    "edaily.co.kr": "이데일리",
    "heraldcorp.com": "헤럴드경제",
    "etoday.co.kr": "이투데이",
    "ajunews.com": "아주경제",
    "newspim.com": "뉴스핌",
    "viva100.com": "브릿지경제",
    "moneys.co.kr": "머니S",
    "ekn.kr": "에너지경제",
    "thebell.co.kr": "더벨",
    "investchosun.com": "인베스트조선",
    "businesspost.co.kr": "비즈니스포스트",
    "newstomato.com": "뉴스토마토",
    "newsway.co.kr": "뉴스웨이",
    "fntoday.co.kr": "파이낸셜투데이",
    "newsprime.co.kr": "프라임경제",
    "joseilbo.com": "조세일보",
    "bizwatch.co.kr": "비즈워치",
    "economist.co.kr": "이코노미스트",
    "magazine.hankyung.com": "한경비즈니스",
    "mediapen.com": "미디어펜",
    "ceoscoredaily.com": "CEO스코어데일리",
    "theguru.co.kr": "더구루",
    "mediasr.co.kr": "미디어SR",
    "einfomax.co.kr": "연합인포맥스",
    # 통신사
    "yna.co.kr": "연합뉴스",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    "mt.co.kr": "머니투데이",
    # IT/전자
    "etnews.com": "전자신문",
    "dt.co.kr": "디지털타임스",
    "zdnet.co.kr": "지디넷코리아",
    "inews24.com": "아이뉴스24",
    # 방송
    "ytn.co.kr": "YTN",
    "sbs.co.kr": "SBS",
    "kbs.co.kr": "KBS",
    "imbc.com": "MBC",
    "mbc.co.kr": "MBC",
    "jtbc.co.kr": "JTBC",
    "tvchosun.com": "TV조선",
    "ichannela.com": "채널A",
    "mbn.co.kr": "MBN",
    "mtn.co.kr": "머니투데이방송",
    "wowtv.co.kr": "한국경제TV",
    "sbscnbc.co.kr": "SBS Biz",
    "yonhapnewstv.co.kr": "연합뉴스TV",

    # 종합/시사 온라인 매체
    "ohmynews.com": "오마이뉴스",
    "pressian.com": "프레시안",
    "sisajournal.com": "시사저널",
    "sisain.co.kr": "시사IN",
    "sisafocus.co.kr": "시사포커스",
    "polinews.co.kr": "폴리뉴스",
    "ilyo.co.kr": "일요신문",
    "ilyoseoul.co.kr": "일요서울",
    "breaknews.com": "브레이크뉴스",
    "newsian.co.kr": "뉴시안",
    "newsfreezone.co.kr": "뉴스프리존",
    "upinews.kr": "UPI뉴스",
    "viewsnnews.com": "뷰스앤뉴스",
    "weekly.chosun.com": "주간조선",
    "weekly.donga.com": "주간동아",
    "weekly.khan.co.kr": "주간경향",
    "newspost.kr": "뉴스포스트",
    "lawissue.co.kr": "로이슈",
    "tf.co.kr": "더팩트",
    "gukjenews.com": "국제뉴스",

    # 경제/금융/산업 온라인 매체
    "enewstoday.co.kr": "이뉴스투데이",
    "ezyeconomy.com": "이지경제",
    "news2day.co.kr": "뉴스투데이",
    "newsworks.co.kr": "뉴스웍스",
    "biztribune.co.kr": "비즈트리뷴",
    "dailyimpact.co.kr": "데일리임팩트",
    "straightnews.co.kr": "스트레이트뉴스",
    "sisaweek.com": "시사위크",
    "whitepaper.co.kr": "화이트페이퍼",
    "smartfn.co.kr": "스마트에프엔",
    "dealsite.co.kr": "딜사이트",
    "ebn.co.kr": "EBN산업경제",
    "paxetv.com": "팍스경제TV",
    "sentv.co.kr": "서울경제TV",
    "fetv.co.kr": "FETV",
    "goodkyung.com": "굿모닝경제",
    "meconomynews.com": "시장경제신문",
    "cstimes.com": "컨슈머타임스",
    "consumernews.co.kr": "소비자가 만드는 신문",
    "datanews.co.kr": "데이터뉴스",
    "sisajournal-e.com": "시사저널e",
    "econovill.com": "이코노믹리뷰",
    "insightkorea.co.kr": "인사이트코리아",
    "econonews.co.kr": "이코노뉴스",
    "g-enews.com": "글로벌이코노믹",
    "seoulfn.com": "서울파이낸스",
    "finomy.com": "현대경제신문",
    "nextdaily.co.kr": "넥스트데일리",
    "infostockdaily.co.kr": "인포스탁데일리",
    "topdaily.kr": "톱데일리",
    "bizhankook.com": "비즈한국",
    "thevaluenews.co.kr": "더밸류뉴스",
    "thepublic.kr": "더퍼블릭",
    "srtimes.kr": "SR타임스",
    "todayeconomic.com": "오늘경제",
    "weeklytrade.co.kr": "한국무역신문",
    "kpinews.kr": "한국정경신문",
    "newsfc.co.kr": "금융소비자뉴스",
    "financialreview.co.kr": "파이낸셜리뷰",
    "s-journal.co.kr": "서울이코노미뉴스",
    "theviewers.co.kr": "뷰어스",
    "newslock.co.kr": "뉴스락",
    "newsworker.co.kr": "뉴스워커",
    "worktoday.co.kr": "워크투데이",
    "megaeconomy.co.kr": "메가경제",
    "pointdaily.co.kr": "포인트데일리",
    "vop.co.kr": "민중의소리",

    # 부동산/건설/에너지/산업 전문지
    "dnews.co.kr": "대한경제",
    "ikld.kr": "국토일보",
    "conslove.co.kr": "한국건설신문",
    "hapt.co.kr": "한국아파트신문",
    "aptn.co.kr": "아파트관리신문",
    "housingherald.co.kr": "하우징헤럴드",
    "areyou.co.kr": "아유경제",
    "rcast.co.kr": "리얼캐스트",
    "e2news.com": "이투뉴스",
    "electimes.com": "전기신문",
    "koenergy.co.kr": "한국에너지",
    "energy-news.co.kr": "에너지신문",
    "industrynews.co.kr": "인더스트리뉴스",
    "hellot.net": "헬로티",
    "kharn.kr": "칸",
    "klnews.co.kr": "물류신문",
    "shippingnewsnet.com": "한국해운신문",
    "maritimepress.co.kr": "한국해운신문",

    # 패션/섬유/뷰티 전문지
    "apparelnews.co.kr": "어패럴뉴스",
    "fashionbiz.co.kr": "패션비즈",
    "fashionn.com": "패션엔",
    "fashionseoul.com": "패션서울",
    "ktnews.com": "한국섬유신문",
    "itnk.co.kr": "국제섬유신문",
    "tinnews.co.kr": "TIN뉴스",
    "beautynury.com": "뷰티누리",
    "cosinkorea.com": "코스인코리아",
    "bntnews.co.kr": "bnt뉴스",

    # 유통/소비재/식품/호텔/여행 전문지
    "retailtalk.co.kr": "리테일톡",
    "thescoop.co.kr": "더스쿠프",
    "foodtoday.or.kr": "푸드투데이",
    "foodnews.co.kr": "식품저널",
    "realfoods.co.kr": "리얼푸드",
    "hotelrestaurant.co.kr": "호텔앤레스토랑",
    "traveltimes.co.kr": "여행신문",
    "ttlnews.com": "티티엘뉴스",
    "tournews21.com": "투어코리아",
    "sportsq.co.kr": "스포츠Q",

    # IT/과학/스타트업/게임 전문지
    "bloter.net": "블로터",
    "digitaltoday.co.kr": "디지털투데이",
    "it.chosun.com": "IT조선",
    "ddaily.co.kr": "디지털데일리",
    "ciokorea.com": "CIO Korea",
    "boannews.com": "보안뉴스",
    "aitimes.com": "AI타임스",
    "thelec.kr": "디일렉",
    "kbench.com": "케이벤치",
    "cnet.co.kr": "씨넷코리아",
    "techm.kr": "테크M",
    "platum.kr": "플래텀",
    "venturesquare.net": "벤처스퀘어",
    "byline.network": "바이라인네트워크",
    "betanews.net": "베타뉴스",
    "newstap.co.kr": "뉴스탭",
    "tokenpost.kr": "토큰포스트",
    "blockmedia.co.kr": "블록미디어",
    "coindeskkorea.com": "코인데스크코리아",
    "gamemeca.com": "게임메카",
    "gamefocus.co.kr": "게임포커스",
    "thisisgame.com": "디스이즈게임",

    # ESG/환경/사회공헌 전문지
    "futurechosun.com": "더나은미래",
    "greened.kr": "녹색경제신문",
    "greenpostkorea.co.kr": "그린포스트코리아",
    "hkbs.co.kr": "환경일보",
    "ecojournal.co.kr": "에코저널",
    "ecomedia.co.kr": "환경미디어",
    "lifein.news": "라이프인",

    # 의료/바이오/제약 전문지
    "medicaltimes.com": "메디칼타임즈",
    "medipana.com": "메디파나뉴스",
    "docdocdoc.co.kr": "청년의사",
    "healthchosun.com": "헬스조선",
    "yakup.com": "약업신문",
    "pharmnews.com": "팜뉴스",
    "biospectator.com": "바이오스펙테이터",
    "hitnews.co.kr": "히트뉴스",
    "dailypharm.com": "데일리팜",

    # 지역 언론 - 수도권/강원
    "kyeongin.com": "경인일보",
    "kyeonggi.com": "경기일보",
    "joongboo.com": "중부일보",
    "kgnews.co.kr": "경기신문",
    "kihoilbo.co.kr": "기호일보",
    "incheonilbo.com": "인천일보",
    "kado.net": "강원도민일보",
    "kwnews.co.kr": "강원일보",

    # 지역 언론 - 충청/대전/세종
    "daejonilbo.com": "대전일보",
    "cctoday.co.kr": "충청투데이",
    "ccdailynews.com": "충청일보",
    "jbnews.com": "중부매일",
    "dynews.co.kr": "동양일보",
    "ccdn.co.kr": "충청매일",
    "chungnamilbo.co.kr": "충남일보",
    "goodmorningcc.com": "굿모닝충청",
    "sjpost.co.kr": "세종포스트",

    # 지역 언론 - 호남/제주
    "mdilbo.com": "무등일보",
    "kwangju.co.kr": "광주일보",
    "jnilbo.com": "전남일보",
    "kjdaily.com": "광주매일신문",
    "namdonews.com": "남도일보",
    "jndn.com": "전남매일",
    "jjan.kr": "전북일보",
    "domin.co.kr": "전북도민일보",
    "jeollailbo.com": "전라일보",
    "jemin.com": "제민일보",
    "jejunews.com": "제주일보",
    "jejumaeil.net": "제주매일",
    "jejudomin.co.kr": "제주도민일보",
    "headlinejeju.co.kr": "헤드라인제주",
    "ijejutoday.com": "제주투데이",
    "newsjeju.net": "뉴스제주",
    "ihalla.com": "한라일보",

    # 지역 언론 - 영남
    "busan.com": "부산일보",
    "kookje.co.kr": "국제신문",
    "knnews.co.kr": "경남신문",
    "gnnews.co.kr": "경남일보",
    "idomin.com": "경남도민일보",
    "yeongnam.com": "영남일보",
    "idaegu.com": "대구일보",
    "kyongbuk.co.kr": "경북일보",
    "kbmaeil.com": "경북매일",
    "hidomin.com": "경북도민일보",
    "ksilbo.co.kr": "경상일보",
    "ulsanpress.net": "울산신문",

    # 지역 방송/종교 방송
    "obsnews.co.kr": "OBS경인TV",
    "knn.co.kr": "KNN",
    "kbc.co.kr": "KBC광주방송",
    "tjb.co.kr": "TJB대전방송",
    "cjb.co.kr": "CJB청주방송",
    "jtv.co.kr": "JTV전주방송",
    "ubc.co.kr": "UBC울산방송",
    "jibs.co.kr": "JIBS제주방송",
    "tbs.seoul.kr": "TBS",
    "bbsi.co.kr": "BBS불교방송",
    "cpbc.co.kr": "가톨릭평화방송",
    "biz.sbs.co.kr": "SBS Biz",

    # 스포츠/연예/문화 매체
    "sportsseoul.com": "스포츠서울",
    "sportschosun.com": "스포츠조선",
    "isplus.com": "일간스포츠",
    "stoo.com": "스포츠투데이",
    "osen.co.kr": "OSEN",
    "starnewskorea.com": "스타뉴스",
    "newsen.com": "뉴스엔",
    "sportsworldi.com": "스포츠월드",
    "xportsnews.com": "엑스포츠뉴스",
    "mydaily.co.kr": "마이데일리",
    "tenasia.hankyung.com": "텐아시아",
    "joynews24.com": "조이뉴스24",
    "spotvnews.co.kr": "스포티비뉴스",
    "sportalkorea.com": "스포탈코리아",
    "mhns.co.kr": "문화뉴스",
    "newsinside.kr": "뉴스인사이드",
    "topstarnews.net": "톱스타뉴스",

    # 국내 발행 영문 매체
    "koreatimes.co.kr": "코리아타임스",
    "koreaherald.com": "코리아헤럴드",
    "koreajoongangdaily.joins.com": "코리아중앙데일리",
    "pulse.mk.co.kr": "매일경제 영문",
    "en.yna.co.kr": "연합뉴스 영문",
    "en.fnnews.com": "파이낸셜뉴스 영문",
    "kedglobal.com": "KED Global",
    "koreabizwire.com": "코리아비즈와이어",
    "ajupress.com": "AJU Press",
}

NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
MAX_PAGES = 5          # 페이지당 100건, 최대 500건까지 조회
PAGE_SIZE = 100


def get_window(now_kst: datetime):
    """전일 09:01 ~ 당일 09:00 (KST) 구간을 반환

    예: 7/1 09:00에 발송하면 다음 날(7/2) 09:00 발송 메일은
    7/1 09:01 ~ 7/2 09:00 구간의 기사를 담는다.
    (직전 발송 시각과 겹치지 않도록 1분 버퍼)
    """
    today_9am = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    window_start = (today_9am - timedelta(days=1)) + timedelta(minutes=1)  # 전일 09:01
    return window_start, today_9am


def strip_tags(text: str) -> str:
    text = re.sub(r"<.*?>", "", text or "")
    return (
        text.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
    )


def fetch_naver_news(client_id: str, client_secret: str, query: str):
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    items = []
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE + 1
        if start > 1000:  # 네이버 API 상한
            break
        params = {
            "query": query,
            "display": PAGE_SIZE,
            "start": start,
            "sort": "date",  # 최신순 정렬
        }
        resp = requests.get(NAVER_SEARCH_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items", [])
        if not batch:
            break
        for it in batch:
            # 이 기사를 찾아낸 검색 키워드를 기록해둡니다. 나중에
            # filter_and_dedupe에서 이 키워드가 실제로 붙어서 등장하는지
            # 재검증할 때 사용합니다.
            it["_query"] = query
        items.extend(batch)
        # 이미 가져온 마지막 기사가 필터 시작 시각보다 이전이면 더 가져올 필요 없음(최신순이므로)
        last_pubdate = parse_pubdate(batch[-1]["pubDate"])
        window_start, _ = get_window(datetime.now(KST))
        if last_pubdate < window_start:
            break
        if len(batch) < PAGE_SIZE:
            break
    return items


def fetch_group_raw_items(client_id: str, client_secret: str, keywords):
    """같은 섹션(브랜드 그룹)에 속한 키워드들을 모두 검색해서 raw item을 합칩니다.
    중복 제거는 filter_and_dedupe에서 링크 기준으로 처리합니다."""
    raw_items = []
    for kw in keywords:
        raw_items.extend(fetch_naver_news(client_id, client_secret, kw))
    return raw_items


def parse_pubdate(pubdate_str: str) -> datetime:
    # 예: 'Wed, 01 Jul 2026 08:12:00 +0900'
    dt = datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")
    return dt.astimezone(KST)


def get_press_name(item: dict) -> str:
    """originallink 도메인에서 언론사를 추정. PRESS_NAME_MAP에 있으면 한글 매체명,
    없으면 도메인 그대로 표시. originallink가 없으면 link 도메인 사용."""
    for key in ("originallink", "link"):
        url = item.get(key) or ""
        if not url:
            continue
        netloc = urlparse(url).netloc
        if not netloc or "naver.com" in netloc:
            continue
        domain = netloc.replace("www.", "")
        # 정확히 일치하는 도메인 우선, 없으면 더 긴(구체적인) 서브도메인부터 매칭
        if domain in PRESS_NAME_MAP:
            return PRESS_NAME_MAP[domain]
        for mapped_domain in sorted(PRESS_NAME_MAP, key=len, reverse=True):
            if domain.endswith(mapped_domain):
                return PRESS_NAME_MAP[mapped_domain]
        return domain
    return "확인불가"


# 기자 이름으로 절대 나올 수 없는 값(칼럼/코너명, 부서명 등). 사이트에서 새로운
# 오탐이 발견되면 여기에 추가하면 됩니다.
NAME_BLACKLIST = {
    "여적", "사설", "만평", "만물상", "특파원", "온라인뉴스", "디지털뉴스",
    "편집국", "취재팀", "뉴스룸", "데스크", "종합", "속보", "단독",
    "인터뷰", "포토", "영상", "그래픽", "카드뉴스", "지면보기", "기자수첩",
}


def is_plausible_reporter_name(name: str) -> bool:
    name = (name or "").strip()
    if not (2 <= len(name) <= 4):
        return False
    if not re.fullmatch(r"[가-힣]+", name):
        return False
    if name in NAME_BLACKLIST:
        return False
    return True


def _extract_json_ld_author(html: str) -> str:
    """schema.org NewsArticle 구조화 데이터(JSON-LD)의 author.name 사용.
    네이버뉴스를 포함한 대부분의 정식 언론사 CMS가 이 표준 태그를
    페이지당 한 번(해당 기사 전용으로) 넣기 때문에, 사이드바/관련기사
    위젯에서 엉뚱한 이름을 주워올 위험이 거의 없어 가장 신뢰도가 높다."""
    for m in re.finditer(r'"author"\s*:\s*(\[?\s*\{.*?\})', html, re.S):
        name_m = re.search(r'"name"\s*:\s*"([^"]{1,20})"', m.group(1))
        if name_m and is_plausible_reporter_name(name_m.group(1)):
            return name_m.group(1).strip()
    return ""


def _extract_naver_head_journalist(html: str) -> str:
    """네이버뉴스 기사 헤더의 기자명 영역. 페이지 안에 같은 클래스가 여러 번
    (관련기사 카드 등) 나올 수 있어, 기사 제목 영역(media_end_head_headline)
    '이후'로 검색 범위를 한정해 실제 기사 작성자를 찾을 확률을 높인다."""
    anchor = html.find("media_end_head_headline")
    area = html[anchor:] if anchor != -1 else html
    m = re.search(r'media_end_head_journalist_name">\s*([^<]+?)\s*<', area)
    if m and is_plausible_reporter_name(m.group(1)):
        return m.group(1).strip()
    return ""


def _extract_meta_author(html: str) -> str:
    m = re.search(
        r'<meta[^>]+name=["\']author["\'][^>]*content=["\']([^"\']{1,30})["\']',
        html,
        re.I,
    )
    if not m:
        return ""
    candidate = re.sub(r"[\(\[].*?[\)\]]", "", m.group(1)).split(",")[0].strip()
    candidate = re.sub(r"\s*기자\s*$", "", candidate).strip()
    return candidate if is_plausible_reporter_name(candidate) else ""


def _extract_cms_byline(html: str) -> str:
    """'김태훈 (다른기사보기)' 처럼 일부 언론사 CMS가 쓰는 정형화된
    작성자 표기 패턴."""
    text = strip_tags(html)
    m = re.search(r"([가-힣]{2,4})\s*\(?\s*다른\s*기사\s*보기", text)
    if m and is_plausible_reporter_name(m.group(1)):
        return m.group(1).strip()
    return ""


def _extract_loose_byline(html: str) -> str:
    """최후의 수단: <head>/<script>/<style>을 제거한 본문 텍스트에서
    'OOO 기자' 패턴을 찾는다. 그래도 관련기사 위젯 등에서 오탐될 수 있으므로
    블랙리스트로 걸러진 값만 채택한다."""
    body = re.sub(r"<head[\s\S]*?</head>", "", html, flags=re.I)
    body = re.sub(r"<script[\s\S]*?</script>", "", body, flags=re.I)
    body = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.I)
    text = strip_tags(body)
    for m in re.finditer(r"([가-힣]{2,4})\s*기자(?!단|회|실)", text):
        if is_plausible_reporter_name(m.group(1)):
            return m.group(1).strip()
    return ""


REPORTER_EXTRACTORS = (
    _extract_json_ld_author,
    _extract_naver_head_journalist,
    _extract_meta_author,
    _extract_cms_byline,
    _extract_loose_byline,
)


def check_keyword_and_get_reporter(item: dict, keyword: str):
    """기사 원문에서 (1) 검색 키워드가 실제로 붙어서(연속된 문자열로) 등장하는지,
    (2) 기자 이름을 함께 확인합니다.

    네이버 뉴스 검색 API는 형태소 단위로 매칭하기 때문에 "넥스가드"를 검색해도
    기사 안에 "넥스"와 "가드"가 따로 떨어져 있기만 해도 결과에 걸릴 수 있습니다.
    이를 걸러내기 위해 제목+요약을 먼저 확인하고, 거기 없으면 기사 본문까지
    확인해서 keyword 문자열이 실제로 붙어있는 경우에만 True를 반환합니다.
    기자 이름 조회는 기존 로직과 동일하며, 어차피 같은 페이지를 열어야 하므로
    키워드 검증과 같은 요청에서 함께 처리해 불필요한 중복 요청을 줄입니다.
    """
    has_keyword = not keyword  # 키워드 정보가 없으면(예외 상황) 검증을 건너뜀
    reporter = ""

    if keyword:
        combined = strip_tags((item.get("title", "") or "") + " " + (item.get("description", "") or ""))
        if keyword in combined:
            has_keyword = True

    link = item.get("link") or ""
    originallink = item.get("originallink") or ""

    candidates = []
    if "news.naver.com" in link:
        candidates.append(link)
    if originallink and originallink not in candidates:
        candidates.append(originallink)
    if link and link not in candidates:
        candidates.append(link)

    for url in candidates:
        if has_keyword and reporter:
            break  # 둘 다 확인됐으면 더 이상 페이지를 열어볼 필요 없음

        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=8)
            resp.raise_for_status()
        except Exception:
            continue
        finally:
            time.sleep(0.15)  # 대상 사이트에 대한 과도한 요청 방지

        html = resp.text

        if not has_keyword and keyword and keyword in strip_tags(html):
            has_keyword = True

        if not reporter:
            for extractor in REPORTER_EXTRACTORS:
                name = extractor(html)
                if name:
                    reporter = name
                    break

    return has_keyword, reporter


def filter_and_dedupe(items, window_start, window_end):
    seen_links = set()
    filtered = []
    for item in items:
        pub = parse_pubdate(item["pubDate"])
        if not (window_start <= pub <= window_end):
            continue
        link = item.get("link") or item.get("originallink")
        if link in seen_links:
            continue

        keyword = item.get("_query", "")
        has_keyword, reporter = check_keyword_and_get_reporter(item, keyword)
        if not has_keyword:
            # 검색 키워드가 형태소만 분리되어 매칭된 오탐 기사 → 결과에서 제외
            continue

        seen_links.add(link)
        filtered.append(
            {
                "title": strip_tags(item.get("title", "")),
                "link": link,
                "originallink": item.get("originallink", ""),
                "press": get_press_name(item),
                "reporter": reporter,
                "pubdate": pub,
                "description": strip_tags(item.get("description", "")),
            }
        )
    filtered.sort(key=lambda x: x["pubdate"], reverse=True)
    return filtered


def build_email_body(group_articles: dict, window_start, window_end) -> str:
    # group_articles: {"크레델리오": [...], "넥스가드": [...], "심파리카": [...]}
    # 전체가 0건일 때는 main()에서 이 함수를 호출하기 전에 이미 메일 발송 자체를
    # 건너뛰므로, 여기서는 최소 1개 섹션에 1건 이상 있다고 가정합니다.
    total = sum(len(v) for v in group_articles.values())
    lines = [
        f"[엘랑코 뉴스 알림] 총 {total}건",
        f"조회 구간: {window_start.strftime('%Y-%m-%d %H:%M')} ~ {window_end.strftime('%Y-%m-%d %H:%M')} (KST)",
        "",
    ]
    for group_name, articles in group_articles.items():
        lines.append(f"■ {group_name} ({len(articles)}건)")
        lines.append("-" * 40)
        if not articles:
            lines.append("해당 기간 신규 기사 없음")
            lines.append("")
            continue
        for i, a in enumerate(articles, 1):
            lines.append(f"{i}. {a['title']}")
            lines.append(f"   언론사: {a['press']}")
            lines.append(f"   기자: {a['reporter'] or '확인불가'}")
            lines.append(f"   발행: {a['pubdate'].strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"   링크: {a['link']}")
            if a["description"]:
                lines.append(f"   요약: {a['description']}")
            lines.append("")
        lines.append("")
    return "\n".join(lines)


def send_email(subject: str, body: str):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    mail_from = os.environ.get("MAIL_FROM", smtp_user)
    mail_to = [addr.strip() for addr in os.environ["MAIL_TO"].split(",") if addr.strip()]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(mail_from, mail_to, msg.as_string())


def main():
    client_id = os.environ["NAVER_CLIENT_ID"]
    client_secret = os.environ["NAVER_CLIENT_SECRET"]

    now_kst = datetime.now(KST)
    window_start, window_end = get_window(now_kst)

    group_articles = {}
    for group_name, keywords in QUERY_GROUPS.items():
        raw_items = fetch_group_raw_items(client_id, client_secret, keywords)
        group_articles[group_name] = filter_and_dedupe(raw_items, window_start, window_end)

    total = sum(len(v) for v in group_articles.values())
    if total == 0:
        print("해당 시간대에 새 기사가 없어 메일을 발송하지 않습니다.")
        return

    subject = f"[엘랑코 뉴스 알림] {now_kst.strftime('%Y-%m-%d')} 09:00 기준 총 {total}건"
    body = build_email_body(group_articles, window_start, window_end)

    send_email(subject, body)
    summary = ", ".join(f"{name} {len(v)}건" for name, v in group_articles.items())
    print(f"발송 완료: 총 {total}건 ({summary})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"오류 발생: {e}", file=sys.stderr)
        raise
