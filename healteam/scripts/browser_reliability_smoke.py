"""Verify S2 browser recovery using an isolated mock database and network faults."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--channel", default="msedge" if sys.platform == "win32" else None)
    parser.add_argument("--output", type=Path, default=Path(".cache/browser-s2"))
    parser.add_argument("--report-session-id", help="Prepared interrupted report in the isolated database")
    parser.add_argument(
        "--simulation-session-id", help="Prepared interrupted simulation in the isolated database"
    )
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
        ready = request.get(args.base_url + "/readyz")
        assert ready.ok and ready.json().get("model_mode") == "mock", "Requires an isolated mock API"

        def api_session(session_id: str) -> dict:
            response = request.get(args.base_url + f"/api/v1/sessions/{session_id}")
            assert response.ok, response.text()
            return response.json()

        def human_events(session_id: str) -> list[dict]:
            return [event for event in api_session(session_id)["events"] if event["source"] == "human"]

        def draft_key(session_id: str) -> str:
            return f"healteam:session-draft:v1:{session_id}"

        def stored(session_id: str):
            return page.evaluate("key => JSON.parse(sessionStorage.getItem(key))", draft_key(session_id))

        def overflow() -> bool:
            return page.evaluate("document.documentElement.scrollWidth > innerWidth")

        try:
            # A healthy HTTP process is insufficient when the database cannot
            # be owned. The homepage must use readiness before enabling create.
            page.route(
                "**/readyz",
                lambda route: route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"status": "unavailable", "model_mode": "mock"}),
                ),
            )
            page.goto(args.base_url, wait_until="networkidle")
            expect(page.get_by_role("button", name="开始亲自练习")).to_be_disabled()
            expect(page.locator(".inline-alert[role='alert']")).to_contain_text("暂时无法读取")
            page.unroute("**/readyz")
            page.get_by_role("button", name="重新连接", exact=True).click()
            expect(page.get_by_role("button", name="开始亲自练习")).to_be_enabled()
            checks.append("readiness_failure_disables_create_and_can_reconnect")

            # Commit create, then lose only its response. Reloading must retain
            # both the configuration and its original idempotency identity.
            create_pattern = re.compile(r"/api/v1/sessions$")
            create_attempts: list[dict] = []

            def lose_create_response(route):
                create_attempts.append(route.request.post_data_json)
                if len(create_attempts) == 1:
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    created.append(response.json()["session_id"])
                    route.abort("failed")
                else:
                    route.continue_()

            page.route(create_pattern, lose_create_response)
            page.locator('label.choice-card:has(input[name="school"][value="satir"])').click()
            page.get_by_label("最大轮次", exact=True).fill("5")
            page.get_by_role("button", name="开始亲自练习").click()
            expect(page.locator(".setup-form .form-alert")).to_be_visible()
            page.reload(wait_until="networkidle")
            expect(page.locator('input[name="school"][value="satir"]')).to_be_checked()
            expect(page.get_by_label("最大轮次", exact=True)).to_have_value("5")
            page.get_by_role("button", name="开始亲自练习").click()
            session_id = created[0]
            page.wait_for_url(re.compile(rf"/sessions/{session_id}$"))
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled()
            assert len(create_attempts) == 2 and create_attempts[0] == create_attempts[1], create_attempts
            page.unroute(create_pattern, lose_create_response)
            assert page.evaluate("sessionStorage.getItem('healteam:create-draft:v1')") is None
            checks.append("ambiguous_create_survives_reload_and_reuses_request_id")

            first = "我想先听听，今天每个人希望我们理解什么。"
            composer = page.get_by_label("你的干预", exact=True)
            composer.fill(first)
            page.reload(wait_until="domcontentloaded")
            expect(composer).to_have_value(first)
            expect(composer).to_be_enabled()
            checks.append("unsent_draft_survives_page_reload")

            # Leave a POST unanswered. The browser deadline must release the
            # busy UI while keeping exactly the original command for retry.
            message_pattern = re.compile(rf"/api/v1/sessions/{session_id}/messages$")
            attempts: list[dict] = []
            held_routes: list = []

            def stall_once(route):
                attempts.append(route.request.post_data_json)
                if len(attempts) == 1:
                    held_routes.append(route)
                else:
                    route.continue_()

            page.route(message_pattern, stall_once)
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator(".pending-message")).to_be_visible(timeout=19000)
            expect(page.locator(".action-alert")).to_contain_text("超时")
            expect(composer).to_be_disabled()
            for held in held_routes:
                try:
                    held.abort("timedout")
                except Exception:
                    pass  # The browser deadline may already have aborted it.
            held_routes.clear()
            pending = stored(session_id)["attempt"]
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".pending-message")).to_be_visible()
            assert stored(session_id)["attempt"] == pending
            page.get_by_role("button", name="重试这条干预", exact=True).click()
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(1)
            expect(composer).to_be_enabled(timeout=15000)
            assert attempts == [pending, pending], attempts
            assert stored(session_id) is None
            page.unroute(message_pattern, stall_once)
            checks.append("post_timeout_reload_and_same_command_retry_without_duplication")

            stream_pattern = re.compile(rf"/api/v1/sessions/{session_id}/events(?:\?|$)")
            read_pattern = re.compile(rf"/api/v1/sessions/{session_id}$")
            block_reads = [False]

            def maybe_block_read(route):
                if block_reads[0]:
                    route.abort("failed")
                else:
                    route.continue_()

            def block_stream(route):
                route.abort("failed")

            page.route(stream_pattern, block_stream)
            page.route(read_pattern, maybe_block_read)
            page.reload(wait_until="domcontentloaded")
            expect(composer).to_be_enabled()
            expect(page.locator(".connection-status")).to_have_text("正在重新连接", timeout=10000)

            accepted_attempts: list[dict] = []

            def lose_message_response(route):
                accepted_attempts.append(route.request.post_data_json)
                response = route.fetch()
                assert response.status == 202, response.text()
                if len(accepted_attempts) == 1:
                    block_reads[0] = True
                    route.abort("failed")
                else:
                    route.fulfill(response=response)

            page.route(message_pattern, lose_message_response)
            second = "请妈妈和孩子分别说说刚才的感受。"
            composer.fill(second)
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator(".pending-message")).to_be_visible()
            accepted_command = stored(session_id)["attempt"]
            assert (
                sum(
                    event["payload"].get("client_message_id") == accepted_command["client_message_id"]
                    for event in human_events(session_id)
                )
                == 1
            )
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="暂时无法打开会话")).to_be_visible()
            assert stored(session_id)["attempt"] == accepted_command
            block_reads[0] = False
            page.get_by_role("button", name="重新读取", exact=True).click()
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(2)
            expect(page.locator(".pending-message")).to_have_count(0)
            expect(composer).to_have_value("")
            expect(composer).to_be_enabled(timeout=15000)
            assert stored(session_id) is None
            page.unroute(message_pattern, lose_message_response)
            checks.append("accepted_response_loss_is_reconciled_by_exact_id_after_reload")

            # Losing a response a second time exercises the explicit retry
            # while the server already owns and has finished that same turn.
            replay_attempts: list[dict] = []

            def lose_then_replay(route):
                replay_attempts.append(route.request.post_data_json)
                response = route.fetch()
                assert response.status == 202, response.text()
                if len(replay_attempts) == 1:
                    block_reads[0] = True
                    route.abort("failed")
                else:
                    route.fulfill(response=response)

            page.route(message_pattern, lose_then_replay)
            composer.fill("爸爸刚才听见了什么，请先用自己的话说说。")
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator(".pending-message")).to_be_visible()
            page.get_by_role("button", name="重试这条干预", exact=True).click()
            expect(page.locator(".pending-message")).to_have_count(0)
            assert len(replay_attempts) == 2 and replay_attempts[0] == replay_attempts[1]
            assert (
                sum(
                    event["payload"].get("client_message_id") == replay_attempts[0]["client_message_id"]
                    for event in human_events(session_id)
                )
                == 1
            )
            block_reads[0] = False
            page.unroute(message_pattern, lose_then_replay)
            page.unroute(read_pattern, maybe_block_read)
            page.unroute(stream_pattern, block_stream)
            page.get_by_role("button", name="重新连接", exact=True).click()
            expect(page.locator(".connection-status")).to_have_text("更新已连接", timeout=15000)
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(3)
            expect(composer).to_be_enabled(timeout=15000)
            checks.append("accepted_command_can_be_replayed_without_a_second_human_turn")

            # Client navigation leaves the old request alive. Its late success
            # must not clear a new draft in a newly mounted workspace.
            late_message: dict = {}

            def hold_accepted_response(route):
                response = route.fetch()
                assert response.status == 202, response.text()
                late_message.update(route=route, response=response)

            page.route(message_pattern, hold_accepted_response)
            composer.fill("我们停一下，听听孩子现在最想补充什么。")
            page.get_by_role("button", name="发送干预").click()
            page.get_by_role("link", name="← 返回首页", exact=True).click()
            expect(page.get_by_role("heading", name="准备这次练习")).to_be_visible()
            page.locator(f'li[data-session-id="{session_id}"] a').click()
            expect(composer).to_be_enabled(timeout=15000)
            new_draft = "这是一份重新进入后写下的新草稿。"
            composer.fill(new_draft)
            assert late_message
            with page.expect_response(lambda response: response.url.endswith(f"/{session_id}/messages")):
                late_message["route"].fulfill(response=late_message["response"])
            page.wait_for_timeout(100)
            expect(composer).to_have_value(new_draft)
            assert stored(session_id)["content"] == new_draft
            page.unroute(message_pattern, hold_accepted_response)
            checks.append("late_post_response_cannot_clear_newly_mounted_workspace_draft")

            page.set_viewport_size({"width": 375, "height": 844})
            composer.fill("这份草稿在断线期间保留。")
            context.set_offline(True)
            expect(page.locator(".connection-alert")).to_contain_text("当前浏览器离线")
            expect(composer).to_have_value("这份草稿在断线期间保留。")
            assert not overflow()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(output / "s2-offline-mobile.png"), full_page=True, animations="disabled")
            context.set_offline(False)
            expect(page.locator(".connection-status")).to_have_text("更新已连接", timeout=15000)
            expect(composer).to_have_value("这份草稿在断线期间保留。")
            checks.append("mobile_offline_banner_draft_preservation_and_online_reconnect")

            page.get_by_role("button", name="结束练习并复盘", exact=True).click()
            expect(page.locator(".report-document")).to_be_visible(timeout=15000)
            assert stored(session_id) is None
            expect(page.locator(".session-heading-status .status-badge")).to_have_text("已完成")
            assert not overflow()
            checks.append("terminal_session_clears_tab_draft_and_preserves_report")

            if args.report_session_id:
                before = api_session(args.report_session_id)
                before_utterances = [event for event in before["events"] if event["type"] == "utterance"]
                assert before["state"] == "interrupted" and before["phase"] != "simulation"
                old_report: dict = {}
                report_pattern = re.compile(rf"/api/v1/reports/{args.report_session_id}$")

                def hold_old_report(route):
                    if not old_report:
                        response = route.fetch()
                        assert response.ok and response.json()["status"] == "failed"
                        old_report.update(route=route, response=response)
                    else:
                        route.continue_()

                page.route(report_pattern, hold_old_report)
                page.goto(
                    args.base_url + f"/sessions/{args.report_session_id}?tab=report",
                    wait_until="domcontentloaded",
                )
                expect(page.get_by_role("button", name="重试生成复盘", exact=True)).to_be_enabled()
                expect(page.locator(".report-failed")).to_contain_text("不会重复家庭对话")
                page.get_by_role("button", name="重试生成复盘", exact=True).click()
                expect(page.locator(".report-document")).to_be_visible(timeout=15000)
                after = api_session(args.report_session_id)
                assert [
                    event for event in after["events"] if event["type"] == "utterance"
                ] == before_utterances
                assert after["state"] == "completed" and after["report_state"] == "ready"
                assert old_report
                with page.expect_response(
                    lambda response: response.url.endswith(f"/reports/{args.report_session_id}")
                ):
                    old_report["route"].fulfill(response=old_report["response"])
                page.wait_for_timeout(100)
                expect(page.locator(".report-document")).to_be_visible()
                page.unroute(report_pattern, hold_old_report)
                checks.append("late_failed_report_response_cannot_replace_recovered_report")
                assert not overflow()
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path=str(output / "s2-recovered-report-mobile.png"), full_page=True, animations="disabled"
                )
                checks.append("interrupted_report_retries_without_replaying_conversation")

            if args.simulation_session_id:
                before = api_session(args.simulation_session_id)
                assert before["state"] == "interrupted" and before["phase"] == "simulation"
                page.goto(
                    args.base_url + f"/sessions/{args.simulation_session_id}?tab=report",
                    wait_until="domcontentloaded",
                )
                expect(page.get_by_role("heading", name="本次会话已中断")).to_be_visible()
                expect(page.get_by_role("button", name="重试生成复盘", exact=True)).to_have_count(0)
                expect(page.get_by_role("link", name="开始新的练习 ↗", exact=True)).to_be_visible()
                assert api_session(args.simulation_session_id)["last_event_seq"] == before["last_event_seq"]
                checks.append("interrupted_simulation_remains_read_only")

            assert not page_errors, page_errors
            result = {
                "status": "passed",
                "checks": checks,
                "page_errors": page_errors,
                "viewport": {"width": 375, "height": 844},
            }
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            page.screenshot(path=str(output / "failure.png"), full_page=True, animations="disabled")
            (output / "failure.json").write_text(
                json.dumps({"checks": checks, "page_errors": page_errors}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            raise
        finally:
            context.set_offline(False)
            for session_id in created:
                try:
                    request.post(args.base_url + f"/api/v1/sessions/{session_id}/cancel")
                except Exception:
                    pass
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
