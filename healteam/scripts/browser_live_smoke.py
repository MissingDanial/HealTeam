"""Verify S3 live UI only against an explicitly configured local model fixture."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument(
        "--provider-url", required=True, help="Loopback-only fake provider with /fixture/health"
    )
    parser.add_argument("--fixture-model", default="healteam-local-fixture")
    parser.add_argument(
        "--allow-local-live-test", action="store_true", help="Acknowledge an isolated local live fixture"
    )
    parser.add_argument("--channel", default="msedge" if sys.platform == "win32" else None)
    parser.add_argument("--output", type=Path, default=Path(".cache/browser-s3"))
    args = parser.parse_args()
    for value in (args.base_url, args.provider_url):
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("Both application and model fixture must use a loopback HTTP address")
    if not args.allow_local_live_test:
        parser.error("Use --allow-local-live-test only with an isolated local model fixture")
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
        fixture = request.get(args.provider_url.rstrip("/") + "/fixture/health")
        assert fixture.ok and fixture.json().get("test_fixture") is True, "No local model fixture marker"
        health = request.get(args.base_url + "/healthz")
        assert health.ok and health.json().get("model_mode") == "live", "Requires explicit live test mode"
        assert health.json().get("model") == args.fixture_model, "Refusing to use a nonfixture model"
        ready = request.get(args.base_url + "/readyz")
        assert ready.ok, ready.text()

        def saved(session_id: str) -> dict:
            response = request.get(args.base_url + f"/api/v1/sessions/{session_id}")
            assert response.ok, response.text()
            return response.json()

        def wait_state(session_id: str, *states: str) -> dict:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                view = saved(session_id)
                if view["state"] in states:
                    return view
                page.wait_for_timeout(50)
            raise AssertionError(f"Session did not reach {states}: {view}")

        def home() -> None:
            page.goto(args.base_url, wait_until="networkidle")
            expect(page.get_by_role("button", name="开始亲自练习")).to_be_enabled()

        def create(rounds: int, mode: str = "training") -> str:
            home()
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
            expect(page.locator(".session-heading")).to_be_visible()
            return session_id

        def screenshot(name: str) -> None:
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(output / name), full_page=True, animations="disabled")

        def control(mode: str) -> None:
            response = request.post(args.provider_url.rstrip("/") + "/fixture/control", data={"mode": mode})
            assert response.ok, response.text()

        try:
            home()
            expect(page.locator(".mode-notice")).to_contain_text("真实模型练习")
            expect(page.locator(".mode-notice")).to_contain_text("病例与对话将发送")
            expect(page.locator(".mode-notice")).to_contain_text("可能产生费用")
            expect(page.locator(".mode-notice")).to_contain_text("未经专家校准")
            screenshot("s3-live-home.png")
            checks.append("live_mode_disclosure_and_creation_enabled")

            session_id = create(2)
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled()
            page.get_by_label("你的干预", exact=True).fill("请爸爸先说说，今天你最希望对话带来什么。")
            page.get_by_role("button", name="发送干预").click()
            expect(page.locator('.utterance[data-source="human"]')).to_have_count(1)
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled(timeout=30000)
            first = saved(session_id)
            assert first["model_mode"] == "live" and first["completed_rounds"] == 1
            assert any(event["actor"] == "father" and event["source"] == "ai" for event in first["events"])
            expect(page.locator('.utterance[data-actor="doctor"]')).to_have_count(0)
            expect(page.locator(".usage-panel")).to_contain_text("调用次数")
            expect(page.locator(".transcript")).not_to_contain_text("预设回应")
            screenshot("s3-live-training.png")
            checks.append("human_turn_uses_live_family_without_ai_doctor")

            page.get_by_label("你的干预", exact=True).fill("请妈妈说说，听到刚才的话你有什么感受。")
            page.get_by_role("button", name="发送干预").click()
            complete = wait_state(session_id, "completed", "failed")
            assert complete["state"] == "completed", complete
            assert complete["usage"]["requests"] >= 3
            assert complete["usage"]["input_tokens"] > 0 and complete["usage"]["output_tokens"] > 0
            assert complete["usage"]["unknown_requests"] == 0
            assert not any(event["visibility"] in {"internal", "supervisor"} for event in complete["events"])
            page.get_by_role("tab", name=re.compile("AI 辅助复盘")).click()
            expect(page.get_by_role("heading", name="AI 辅助复盘", exact=True)).to_be_visible()
            expect(page.locator(".report-heading")).to_contain_text("未经专家校准")
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            report = request.get(args.base_url + f"/api/v1/reports/{session_id}").json()
            assert report["kind"] == "ai_review" and report["structured"]
            screenshot("s3-ai-review.png")
            checks.append("structured_ai_review_and_saved_usage")

            evidence = page.locator(".review-evidence button").first
            expect(evidence).to_be_visible()
            seq = int(re.search(r"#(\d+)", evidence.inner_text()).group(1))
            evidence.click()
            expect(page.get_by_role("tab", name=re.compile("治疗对话"))).to_have_attribute(
                "aria-selected", "true"
            )
            target = page.locator(f'[data-event-seq="{seq}"]')
            expect(target).to_be_focused()
            page.get_by_role("tab", name=re.compile("AI 辅助复盘")).click()
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            assert request.get(args.base_url + f"/api/v1/reports/{session_id}").json() == report
            home()
            page.locator(f'li[data-session-id="{session_id}"] a').click()
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            checks.append("evidence_navigation_refresh_and_history_reopen")

            demo_id = create(1, mode="demo")
            demo = wait_state(demo_id, "completed", "failed")
            assert demo["state"] == "completed", demo
            assert not any(event["source"] == "human" for event in demo["events"])
            assert any(event["actor"] == "doctor" for event in demo["events"])
            page.get_by_role("tab", name=re.compile("AI 辅助复盘")).click()
            expect(page.locator(".report-intro")).to_contain_text("不代表学员成绩")
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            checks.append("live_demo_is_distinct_from_learner_performance")

            for fixture_mode, expected_code, expected_message in (
                ("auth_error", "llm_http_401", "未通过身份验证"),
                ("wrong_actor", "model_role_output_invalid", "未通过格式或证据核对"),
            ):
                control(fixture_mode)
                failed_id = create(1)
                page.get_by_label("你的干预", exact=True).fill("请爸爸先说说你现在的想法。")
                page.get_by_role("button", name="发送干预").click()
                failed = wait_state(failed_id, "failed", "completed")
                assert failed["state"] == "failed" and failed["error_code"] == expected_code, failed
                assert len([event for event in failed["events"] if event["source"] == "human"]) == 1
                assert not [
                    event
                    for event in failed["events"]
                    if event["type"] == "utterance" and event["source"] == "ai"
                ]
                expect(page.locator(".inline-alert[role='alert']")).to_contain_text(expected_message)
                assert request.get(args.base_url + f"/api/v1/reports/{failed_id}").json()["status"] != "ready"
                control("normal")
                checks.append(f"{fixture_mode}_is_visible_without_mock_fallback")

            retry_id = create(3)
            page.get_by_label("你的干预", exact=True).fill("请妈妈先说说，刚才你最希望被听到的事。")
            page.get_by_role("button", name="发送干预").click()
            expect(page.get_by_label("你的干预", exact=True)).to_be_enabled(timeout=30000)
            before_retry = saved(retry_id)
            dialogue_before = [event for event in before_retry["events"] if event["type"] == "utterance"]
            control("bad_evidence")
            page.get_by_role("button", name="结束练习并复盘").click()
            failed_review = wait_state(retry_id, "failed", "completed")
            assert failed_review["state"] == "failed" and failed_review["report_state"] == "failed", (
                failed_review
            )
            expect(page.get_by_role("button", name="重试生成复盘")).to_be_visible(timeout=15000)
            expect(page.locator(".report-failed")).to_contain_text("可能产生费用")
            control("normal")
            page.get_by_role("button", name="重试生成复盘").click()
            recovered = wait_state(retry_id, "completed", "failed")
            assert recovered["state"] == "completed", recovered
            assert [event for event in recovered["events"] if event["type"] == "utterance"] == dialogue_before
            assert recovered["usage"]["requests"] > failed_review["usage"]["requests"]
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            checks.append("invalid_report_evidence_can_retry_without_replaying_dialogue")

            page.set_viewport_size({"width": 375, "height": 844})
            page.reload(wait_until="domcontentloaded")
            expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            expect(page.locator(".usage-panel")).to_contain_text("预算预留")
            screenshot("s3-live-report-mobile.png")
            home()
            expect(page.locator(".mode-notice")).to_contain_text("可能产生费用")
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            screenshot("s3-live-home-mobile.png")
            checks.append("mobile_live_disclosure_review_and_usage_without_overflow")

            for body in (health.json(), ready.json(), complete, report):
                assert "api_key" not in body and "base_url" not in body
            checks.append("public_views_exclude_credentials_and_private_events")
            assert not page_errors, page_errors
            (output / "result.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "checks": checks,
                        "page_errors": page_errors,
                        "external_paid_model_calls": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {"status": "passed", "checks": checks, "page_errors": page_errors}, ensure_ascii=False
                )
            )
        except Exception:
            screenshot("failure.png")
            (output / "failure-dom.html").write_text(page.content(), encoding="utf-8")
            raise
        finally:
            try:
                control("normal")
            except Exception:
                pass
            for session_id in created:
                try:
                    request.post(args.base_url + f"/api/v1/sessions/{session_id}/cancel")
                except Exception:
                    pass
            page.unroute_all(behavior="ignoreErrors")
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
