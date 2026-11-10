"""Exercise the real S1 Next interface against an isolated, explicit mock API."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--channel", default="msedge" if sys.platform == "win32" else None)
    parser.add_argument("--output", type=Path, default=Path(".cache/browser-s1"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks: list[str] = []
    page_errors: list[str] = []
    created: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=args.channel)
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        request = context.request
        health = request.get(args.base_url + "/healthz")
        assert health.ok and health.json().get("model_mode") == "mock", (
            "Only run against an explicit mock API"
        )

        def home() -> None:
            page.goto(args.base_url, wait_until="networkidle")
            expect(page.get_by_role("heading", name="准备这次练习")).to_be_visible()
            expect(page.get_by_role("button", name="开始亲自练习")).to_be_enabled()

        def create_session(rounds: int, *, mode: str = "training", school: str = "satir") -> str:
            home()
            page.locator(f'label.choice-card:has(input[name="school"][value="{school}"])').click()
            page.locator(f'label.choice-card:has(input[name="mode"][value="{mode}"])').click()
            page.get_by_label("最大轮次", exact=True).fill(str(rounds))
            with page.expect_response(
                lambda response: (
                    urlparse(response.url).path == "/api/v1/sessions" and response.request.method == "POST"
                )
            ) as captured:
                page.get_by_role(
                    "button", name="开始亲自练习" if mode == "training" else "开始自动演示"
                ).click()
            response = captured.value
            assert response.status == 201, response.text()
            session_id = response.json()["session_id"]
            created.append(session_id)
            page.wait_for_url(re.compile(rf"/sessions/{session_id}(?:\?.*)?$"))
            # An active SSE subscription intentionally prevents networkidle.
            expect(page.locator(".session-heading")).to_be_visible()
            return session_id

        try:
            home()
            expect(page.locator('input[name="mode"][value="training"]')).to_be_checked()
            page.screenshot(path=str(output / "s1-home.png"), full_page=True, animations="disabled")
            checks.append("homepage_defaults_and_case_configuration")

            session_id = create_session(3)
            first = "我想先听听，每个人最希望今天的对话带来什么。"
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled()
            page.get_by_label("你的干预", exact=True).fill(first)
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(1)
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled(timeout=15000)
            expect(page.locator('.utterance[data-actor="doctor"]')).to_have_count(0)
            expect(page.locator('.utterance[data-source="human"]')).to_contain_text("你 · 治疗师")
            page.screenshot(
                path=str(output / "s1-training-desktop.png"), full_page=True, animations="disabled"
            )
            checks.append("human_therapist_turn_and_no_ai_doctor")

            # Abort one real EventSource subscription, then allow native retry.
            stream_pattern = re.compile(r"/api/v1/sessions/[0-9a-f-]+/events(?:\?|$)")
            interrupted = [False]

            def interrupt_once(route):
                if not interrupted[0]:
                    interrupted[0] = True
                    route.abort("failed")
                else:
                    route.continue_()

            page.route(stream_pattern, interrupt_once)
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".connection-status")).to_have_text("正在重新连接", timeout=10000)
            expect(page.locator(".connection-status")).to_have_text("更新已连接", timeout=15000)
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(1)
            expect(page.locator('.utterance[data-source="human"]')).to_contain_text(first)
            page.unroute(stream_pattern, interrupt_once)
            checks.append("refresh_and_native_sse_reconnect_without_duplicates")

            # A rejected command must not pin the next attempt to a stale turn.
            message_pattern = re.compile(rf"/api/v1/sessions/{session_id}/messages$")
            attempts: list[dict] = []

            def conflict_once(route):
                attempts.append(route.request.post_data_json)
                if len(attempts) == 1:
                    route.fulfill(
                        status=409,
                        content_type="application/json",
                        body=json.dumps({"detail": {"code": "turn_conflict"}}),
                    )
                else:
                    route.continue_()

            page.route(message_pattern, conflict_once)
            third = "我想请你们轮流说说，刚才哪句话最希望被理解。"
            page.get_by_label("你的干预", exact=True).fill(third)
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator(".action-alert[role='alert']")).to_contain_text("会话状态已更新")
            expect(page.get_by_label("你的干预", exact=True)).to_have_value(third)
            external = request.post(
                args.base_url + f"/api/v1/sessions/{session_id}/messages",
                data={
                    "client_message_id": str(uuid4()),
                    "expected_turn": 2,
                    "content": "另一个窗口中保存的第二轮干预。",
                },
            )
            assert external.status == 202, external.text()
            expect(page.locator(".session-heading-status")).to_contain_text("2 / 3 轮")
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled(timeout=15000)
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator(".session-heading-status .status-badge")).to_have_text(
                "已完成", timeout=20000
            )
            assert (
                len(attempts) == 2 and attempts[0]["expected_turn"] == 2 and attempts[1]["expected_turn"] == 3
            ), attempts
            assert attempts[0]["client_message_id"] != attempts[1]["client_message_id"]
            page.unroute(message_pattern, conflict_once)
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(3)
            expect(page.locator('.utterance[data-actor="doctor"]')).to_have_count(0)
            checks.append("conflict_keeps_draft_and_retries_with_fresh_turn")

            page.get_by_role("tab", name=re.compile("流程复盘")).click()
            expect(page.locator(".report-document")).to_contain_text("离线训练流程验证报告")
            expect(page.locator(".report-document")).to_contain_text(third)
            page.screenshot(path=str(output / "s1-report.png"), full_page=True, animations="disabled")
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".report-document")).to_contain_text("离线训练流程验证报告")
            home()
            page.locator(f'li[data-session-id="{session_id}"] a').click()
            expect(page.locator(".report-document")).to_contain_text(third)
            checks.append("saved_report_refresh_and_history_reopen")

            # An active session from another window is offered as a continuation.
            active = request.post(
                args.base_url + "/api/v1/sessions",
                data={
                    "client_request_id": str(uuid4()),
                    "case_id": "teen-truancy-01",
                    "school": "structural",
                    "mode": "training",
                    "max_rounds": 4,
                },
            )
            assert active.status == 201, active.text()
            active_id = active.json()["session_id"]
            created.append(active_id)
            home()
            page.get_by_role("button", name="开始亲自练习").click()
            continuation = page.get_by_role("link", name=re.compile("继续已有会话"))
            expect(continuation).to_have_attribute("href", f"/sessions/{active_id}")
            continuation.click()
            expect(page.get_by_role("button", name="取消本次会话")).to_be_visible()
            page.get_by_role("button", name="取消本次会话").click()
            expect(page.locator(".session-heading-status .status-badge")).to_have_text("已取消")
            checks.append("active_session_continuation_and_cancel")

            create_session(1, mode="demo", school="bowen")
            expect(page.locator(".session-heading-status .status-badge")).to_have_text(
                "已完成", timeout=20000
            )
            assert page.locator('.utterance[data-actor="doctor"]').count() >= 1
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(0)
            checks.append("automatic_demo_uses_same_durable_protocol")

            page.set_viewport_size({"width": 375, "height": 844})
            home()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "s1-home-mobile.png"), full_page=True, animations="disabled")
            create_session(3)
            page.get_by_label("你的干预", exact=True).fill("这次我们先从你最想表达的一句话开始。")
            page.get_by_role("button", name="发送干预").click()
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled(timeout=15000)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(
                path=str(output / "s1-training-mobile.png"), full_page=True, animations="disabled"
            )
            page.get_by_role("button", name="结束练习并复盘").click()
            expect(page.locator(".report-document")).to_contain_text("离线训练流程验证报告", timeout=20000)
            checks.append("mobile_composer_and_early_finish")

            assert not page_errors, page_errors
            result = {"status": "passed", "checks": checks, "page_errors": page_errors}
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False))
        except Exception as error:
            result = {"status": "failed", "checks": checks, "error": str(error), "page_errors": page_errors}
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            page.screenshot(path=str(output / "failure.png"), full_page=True, animations="disabled")
            raise
        finally:
            for session_id in created:
                try:
                    snapshot = request.get(args.base_url + f"/api/v1/sessions/{session_id}")
                    if snapshot.ok and snapshot.json()["state"] not in {
                        "completed",
                        "cancelled",
                        "failed",
                        "interrupted",
                    }:
                        request.post(args.base_url + f"/api/v1/sessions/{session_id}/cancel", data={})
                except Exception:
                    pass
            browser.close()


if __name__ == "__main__":
    main()
