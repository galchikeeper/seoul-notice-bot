#!/usr/bin/env python3
"""공고 1건 → 카드 이미지 1장 (PNG)"""
import json, sys, base64, io, html, asyncio
import qrcode
import qrcode.image.svg
from playwright.async_api import async_playwright

W = 900  # 카드 폭(px). 높이는 내용에 따라 자동


def qr_data_uri(url: str) -> str:
    qr = qrcode.QRCode(version=None, box_size=8, border=0,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#17191A", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_html(d: dict) -> str:
    e = html.escape
    rows = ""
    for i, t in enumerate(d["types"], 1):
        rows += f"""
        <tr>
          <td class="no">{i}</td>
          <td class="ty"><b>{e(t['name'])}</b><span>{e(t['area'])}</span></td>
          <td class="mn">{e(t['deposit'])}</td>
          <td class="mn">{e(t['rent'])}</td>
        </tr>"""

    quals = "".join(f"<li>{e(q)}</li>" for q in d["quals"])
    qr = qr_data_uri(d["url"])

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<style>
  @page {{ margin:0 }}
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  :root {{
    --paper:#F2F3F0; --surface:#FFFFFF; --surface2:#EAECE7;
    --ink:#17191A; --muted:#6B706E; --line:#DADDD6; --line2:#B7BCB4;
    --seal:#A32B23; --sealbg:#F6E7E5;
    --slate:#33505E; --slatebg:#E4EAEE;
  }}
  body {{
    width:{W}px; background:var(--paper); color:var(--ink);
    font-family:"Noto Sans CJK KR","Noto Sans KR",sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  .card {{ background:var(--surface); border:1px solid var(--line); }}

  /* 머리 */
  .head {{ padding:40px 44px 30px; border-bottom:3px solid var(--ink); }}
  .kicker {{ font-size:19px; font-weight:700; letter-spacing:.14em; color:var(--seal); margin-bottom:14px; }}
  h1 {{ font-family:"Noto Serif CJK KR",serif; font-size:52px; font-weight:900; line-height:1.18; letter-spacing:-.02em; }}
  .sub {{ margin-top:12px; font-size:24px; color:var(--muted); }}

  /* 마감 띠 */
  .dday {{ display:flex; align-items:baseline; gap:20px; padding:26px 44px;
           background:var(--sealbg); border-bottom:1px solid var(--line); }}
  .dday .lab {{ font-size:22px; font-weight:700; color:var(--seal); letter-spacing:.05em; }}
  .dday .val {{ font-size:30px; font-weight:800; color:var(--ink); }}
  .dday .tag {{ margin-left:auto; background:var(--seal); color:#fff; font-size:28px; font-weight:800;
                padding:6px 20px; border-radius:4px; letter-spacing:.02em; }}

  /* 기본정보 */
  .facts {{ padding:30px 44px; display:grid; grid-template-columns:110px 1fr; gap:16px 24px;
            border-bottom:1px solid var(--line); font-size:25px; }}
  .facts dt {{ color:var(--muted); font-weight:500; }}
  .facts dd {{ font-weight:600; line-height:1.45; }}

  /* 평형표 */
  .sec {{ padding:34px 44px 8px; }}
  .sec h2 {{ font-size:23px; font-weight:800; letter-spacing:.1em; color:var(--muted); }}
  .sec h2 em {{ font-style:normal; font-weight:600; color:var(--line2); margin-left:10px; letter-spacing:0 }}
  table {{ width:100%; border-collapse:collapse; margin:18px 44px 34px; width:calc(100% - 88px); }}
  thead th {{ font-size:21px; font-weight:700; color:var(--muted); text-align:right;
              padding:0 0 12px; border-bottom:2px solid var(--line2); }}
  thead th:nth-child(1) {{ text-align:center; width:56px }}
  thead th:nth-child(2) {{ text-align:left }}
  tbody td {{ padding:20px 0; border-bottom:1px solid var(--line); text-align:right;
              font-variant-numeric:tabular-nums; font-size:31px; font-weight:700; }}
  tbody tr:last-child td {{ border-bottom:none }}
  td.no {{ text-align:center; font-size:24px; font-weight:800; color:var(--seal); }}
  td.ty {{ text-align:left }}
  td.ty b {{ display:block; font-size:27px; font-weight:700 }}
  td.ty span {{ display:block; font-size:23px; font-weight:500; color:var(--muted); margin-top:2px }}

  /* 자격 */
  .qual {{ padding:30px 44px 34px; background:var(--surface2); border-top:1px solid var(--line); }}
  .qual h2 {{ font-size:23px; font-weight:800; letter-spacing:.1em; color:var(--muted); margin-bottom:16px }}
  .qual ul {{ list-style:none; display:grid; grid-template-columns:1fr 1fr; gap:12px 28px }}
  .qual li {{ font-size:25px; font-weight:600; padding-left:22px; position:relative }}
  .qual li::before {{ content:""; position:absolute; left:0; top:14px; width:9px; height:9px;
                      background:var(--slate); border-radius:50% }}

  /* 발 */
  .foot {{ display:flex; align-items:center; gap:26px; padding:30px 44px; border-top:1px solid var(--line); }}
  .foot img {{ width:132px; height:132px; display:block }}
  .foot .txt {{ flex:1 }}
  .foot .t1 {{ font-size:22px; color:var(--muted); font-weight:600; letter-spacing:.06em }}
  .foot .t2 {{ font-size:26px; font-weight:700; margin-top:6px; word-break:break-all; line-height:1.35 }}
  .foot .t3 {{ font-size:20px; color:var(--muted); margin-top:10px }}
</style></head><body>
<div class="card">
  <div class="head">
    <div class="kicker">{e(d['kicker'])}</div>
    <h1>{e(d['name'])}</h1>
    <div class="sub">{e(d['subtitle'])}</div>
  </div>

  <div class="dday">
    <span class="lab">청약 마감</span>
    <span class="val">{e(d['deadline'])}</span>
    <span class="tag">{e(d['dday'])}</span>
  </div>

  <dl class="facts">
    <dt>위치</dt><dd>{e(d['addr'])}<br><span style="color:var(--muted);font-weight:500">{e(d['station'])}</span></dd>
    <dt>공급</dt><dd>{e(d['supply'])}</dd>
    <dt>사업주체</dt><dd style="font-weight:500">{e(d['operator'])}</dd>
  </dl>

  <div class="sec"><h2>평형별 조건 <em>단위: 만원</em></h2></div>
  <table>
    <thead><tr><th>#</th><th>유형 · 전용</th><th>보증금</th><th>월세</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <div class="qual">
    <h2>신청 자격</h2>
    <ul>{quals}</ul>
  </div>

  <div class="foot">
    <img src="{qr}" alt="QR">
    <div class="txt">
      <div class="t1">청약 신청</div>
      <div class="t2">{e(d['url_label'])}</div>
      <div class="t3">QR을 카메라로 찍으면 바로 열립니다</div>
    </div>
  </div>
</div>
</body></html>"""


async def render(d: dict, out: str):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": 100},
                              device_scale_factor=2)
        await pg.set_content(build_html(d), wait_until="networkidle")
        await pg.screenshot(path=out, full_page=True)
        await b.close()


if __name__ == "__main__":
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    asyncio.run(render(data, out))
    print("wrote", out)
