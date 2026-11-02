"""Claude로 스크린샷 + 화면 텍스트에서 프로모션/가격 정보를 구조화해 뽑는다."""
from __future__ import annotations

import base64
import json

import anthropic

NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
CATS = ["tv", "refrigerator", "washer", "other"]
MECHANICS = ["percent_off", "price_drop", "coupon", "cashback", "installments",
             "pix_discount", "free_shipping", "gift_bundle", "trade_in", "flash_sale", "other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["campaigns", "ended_campaign_ids", "products", "page_note", "page_note_en"],
    "properties": {
        "campaigns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title_pt", "title_ko", "title_en", "mechanics", "discount_text", "categories",
                             "brands", "conditions_ko", "conditions_en", "valid_until", "is_hero",
                             "status", "previous_id"],
                "properties": {
                    "title_pt": {"type": "string"},
                    "title_ko": {"type": "string"},
                    "title_en": {"type": "string"},
                    "mechanics": {"type": "array", "items": {"type": "string", "enum": MECHANICS}},
                    "discount_text": STR,
                    "categories": {"type": "array", "items": {"type": "string", "enum": CATS}},
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "conditions_ko": STR,
                    "conditions_en": STR,
                    "valid_until": STR,
                    "is_hero": {"type": "boolean"},
                    "status": {"type": "string", "enum": ["new", "continuing"]},
                    "previous_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                },
            },
        },
        "ended_campaign_ids": {"type": "array", "items": {"type": "integer"}},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["brand", "model", "category", "list_price", "sale_price",
                             "pix_price", "discount_pct", "installments", "seller"],
                "properties": {
                    "brand": {"type": "string"},
                    "model": {"type": "string"},
                    "category": {"type": "string", "enum": CATS},
                    "list_price": NUM,
                    "sale_price": NUM,
                    "pix_price": NUM,
                    "discount_pct": NUM,
                    "installments": STR,
                    "seller": STR,
                },
            },
        },
        "page_note": {"type": "string"},
        "page_note_en": {"type": "string"},
    },
}

SYSTEM = """당신은 LG전자 중남미 D2C팀을 위한 브라질 이커머스 프로모션 분석가입니다.
브라질 쇼핑몰 또는 제조사 공식몰 페이지의 스크린샷 조각들(위에서 아래 순서)과 화면 텍스트를 받아,
현재 진행 중인 프로모션과 상품 가격을 정확히 추출합니다.

규칙:
- 화면에 실제로 보이는 정보만 기록합니다. 추측하거나 지어내지 않습니다. 확실하지 않은 값은 null.
- 대상 카테고리는 TV, 냉장고(geladeira/refrigerador), 세탁기(lavadora/máquina de lavar, 세탁건조기 포함)입니다.
  이 세 카테고리와 무관한 캠페인(패션, 식품 등)은 사이트 전체 행사(예: Black Friday, 사이트 전체 쿠폰)일 때만 categories=["other"]로 기록합니다.
- 캠페인: 배너, 쿠폰 코드, 캐시백, PIX 할인, 무이자 할부(ex. 10x sem juros), 무료배송, 사은품, 보상판매(trade-in), 타임세일 등.
  title_pt는 원문, title_ko는 자연스러운 한국어 번역, title_en은 자연스러운 영어 번역.
  conditions_ko에는 쿠폰코드·최소구매액·카드사 조건 등을 한국어로, conditions_en에는 같은 내용을 영어로.
  is_hero는 페이지 최상단 메인 배너급 캠페인이면 true.
- 상품: TV/냉장고/세탁기만. 가격은 BRL 숫자(1.299,90 -> 1299.9). list_price는 취소선 정가(de), sale_price는 판매가(por),
  pix_price는 PIX/à vista 가격이 따로 표시될 때만. discount_pct는 화면에 표시된 할인율이 있으면 그 값, 없으면 정가·판매가로 계산.
  같은 페이지에서 최대 30개, 화면 위쪽(노출 우선순위가 높은) 상품부터.
- 어제 기록된 캠페인 목록이 주어집니다. 오늘 캠페인이 어제와 같은 행사(문구가 조금 달라도 같은 행사)면
  status="continuing", previous_id=해당 id. 새 행사면 status="new", previous_id=null.
  어제 목록 중 오늘 화면에서 더 이상 보이지 않는 행사의 id는 ended_campaign_ids에 넣습니다.
- page_note: 이 페이지의 프로모션 분위기를 한국어 1~2문장으로 요약 (예: "메인 배너 전체가 Samsung TV 할인 기획전").
  page_note_en: 같은 내용을 영어로.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용


def extract_page(cfg: dict, site_name: str, page_key: str, url: str,
                 tiles: list[bytes], text: str, previous: list[dict]) -> dict:
    content: list[dict] = []
    for i, t in enumerate(tiles):
        content.append({"type": "text", "text": f"[스크린샷 {i + 1}/{len(tiles)}]"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(t).decode()}})
    prev = [{"id": p["id"], "title_pt": p["title_pt"], "discount_text": p["discount_text"]}
            for p in previous]
    content.append({"type": "text", "text": (
        f"사이트: {site_name}\n페이지 종류: {page_key}\nURL: {url}\n\n"
        f"어제 기록된 캠페인 목록(JSON):\n{json.dumps(prev, ensure_ascii=False)}\n\n"
        f"화면 텍스트(일부):\n<page_text>\n{text}\n</page_text>")})

    params = dict(
        model=cfg.get("model", "claude-opus-5-5"),
        max_tokens=32000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.get("effort", "medium"),
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    client = _client()
    if cfg.get("use_fallbacks", True):
        with client.beta.messages.stream(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params) as s:
            msg = s.get_final_message()
    else:
        with client.messages.stream(**params) as s:
            msg = s.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거절함(refusal)")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("출력 길이 초과(max_tokens)")
    text_out = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text_out)


SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ko", "en"],
    "properties": {"ko": {"type": "array", "items": {"type": "string"}},
                   "en": {"type": "array", "items": {"type": "string"}}},
}


def summarize_day(cfg: dict, digest: dict) -> dict[str, str]:
    """하루 변화 요약(Teams/대시보드 상단용). {"ko": "- ...", "en": "- ..."} 마크다운 bullet 문자열."""
    client = _client()
    msg = client.messages.create(
        model=cfg.get("model", "claude-opus-5-5"),
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
        system=("당신은 LG전자 중남미 D2C팀에 매일 아침 경쟁사 프로모션 동향을 보고하는 분석가입니다. "
                "주어진 데이터에 있는 사실만 사용하고, 결론부터 말하는 bullet 3~5개로 작성하세요. "
                "LG가 대응을 검토해야 할 사항(경쟁사의 TV/냉장고/세탁기 신규 할인, 큰 가격 하락, LG 제품이 "
                "쇼핑몰 행사에 포함/제외된 경우)을 우선합니다. "
                "ko에는 한국어 bullet 문장을, en에는 같은 내용의 영어 bullet 문장을 같은 순서로 넣으세요. "
                "각 항목은 '- ' 없이 문장만 씁니다."),
        messages=[{"role": "user", "content": json.dumps(digest, ensure_ascii=False)}],
    )
    if msg.stop_reason == "refusal":
        return {"ko": "- (요약 생성 실패: 모델 거절)", "en": "- (Summary failed: model refusal)"}
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    return {k: "\n".join(f"- {line}" for line in data[k]) for k in ("ko", "en")}
