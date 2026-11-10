"""Browser checks against running S0 API + Next servers, in explicit mock mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--channel", default="msedge" if sys.platform == "win32" else None)
    args = parser.parse_args()
    output = Path(".cache/browser").resolve()
    output.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=args.channel)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: failures.append(str(error)))
            response = page.request.get(args.base_url + "/healthz")
            assert response.ok and response.json().get("model_mode") == "mock", (
                "Only run browser smoke against mock"
            )
            page.goto(args.base_url, wait_until="networkidle")
            expect(page.get_by_role("heading", name="六个协作角色")).to_be_visible()
            page.get_by_role("link", name="打开自动演示").click()
            page.wait_for_load_state("networkidle")
            expect(page.locator("#caseInfoSection")).to_be_visible()
            page.locator("#roundsInput").fill("3")
            page.locator("#schoolSelect").select_option("satir")
            page.get_by_role("button", name="开始模拟").click()
            expect(page.locator("#statStatus")).to_have_text("已完成", timeout=30000)
            expect(page.locator("#statRound")).to_have_text("3 轮")
            expect(page.locator(".round-divider")).to_have_text(["第 1 轮", "第 2 轮", "第 3 轮"])
            expect(page.locator("#chatArea")).to_contain_text("离线流程演示（mock）")
            expect(page.locator("#chatArea")).to_contain_text("离线流程演示报告")
            page.screenshot(path=str(output / "s0-demo.png"), full_page=True)

            # Exercise the real page parser with one-byte network chunks,
            # including multibyte Chinese text, rather than copying its code.
            page.evaluate("""async () => {
              const originalFetch = window.fetch;
              const events = [
                ['start', {max_rounds: 1, model_mode: 'mock'}],
                ['utterance', {round: 1, actor: 'doctor', content: '中文分块验证'}],
                ['session_end', {rounds: 1, total_events: 1}],
                ['complete', {}]
              ];
              const bytes = new TextEncoder().encode(events.map(([type, payload]) =>
                `event: ${type}\\ndata: ${JSON.stringify(payload)}\\n\\n`).join(''));
              window.fetch = async () => new Response(new ReadableStream({start(controller) {
                for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
                controller.close();
              }}), {headers: {'content-type': 'text/event-stream'}});
              try { await startSession(); } finally { window.fetch = originalFetch; }
            }""")
            expect(page.locator("#statStatus")).to_have_text("已完成")
            expect(page.locator("#chatArea .bubble-content")).to_have_text("中文分块验证")
            page.evaluate("""() => {
              window.__hintExecuted = 0;
              addHintBubble(1, '<img src=x onerror="window.__hintExecuted=1">', 'safe', 0.7);
              addHintItem(1, '<img src=x onerror="window.__hintExecuted=1">', 'safe');
            }""")
            assert page.evaluate("window.__hintExecuted") == 0
            assert page.locator(".hint img, .hint-item img").count() == 0
            page.evaluate("""async () => {
              const originalFetch = window.fetch;
              window.fetch = async () => new Response('{}', {status: 422,
                headers: {'content-type': 'application/json'}});
              try { await startSession(); } finally { window.fetch = originalFetch; }
            }""")
            expect(page.locator("#statStatus")).to_have_text("未完成")
            expect(page.locator("#chatArea")).to_contain_text("HTTP 422")
            assert not failures, failures
            result = {
                "status": "passed",
                "checks": [
                    "homepage",
                    "same_origin_case_api",
                    "three_round_satir_mock",
                    "report_badge",
                    "one_byte_sse_chunks",
                    "hint_text_safety",
                    "http_failure_state",
                ],
                "page_errors": failures,
            }
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
