"""S4 browser acceptance against an explicitly verified LOCAL model fixture."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--provider-url", required=True)
    parser.add_argument("--fixture-model", default="healteam-local-fixture")
    parser.add_argument("--allow-local-live-test", action="store_true")
    parser.add_argument("--mock-session-id")
    parser.add_argument(
        "--legacy-session-id", help="Optional completed S3 fixture without rubric or assessment"
    )
    parser.add_argument(
        "--recovery-session-id",
        help="Only verify malformed drafts and conflict recovery on an existing fixture report",
    )
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/browser-s4"))
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    if not args.allow_local_live_test or any(
        urlparse(url).hostname not in {"127.0.0.1", "localhost"} for url in (args.base_url, args.provider_url)
    ):
        parser.error("Only explicitly authorized loopback fixture tests are supported")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks, errors, created = [], [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100}, reduced_motion="reduce", accept_downloads=True
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        request = context.request
        health = request.get(args.base_url + "/healthz").json()
        fixture = request.get(args.provider_url.rstrip("/") + "/fixture/health").json()
        assert fixture.get("test_fixture") is True
        assert health.get("model_mode") == "live" and health.get("model") == args.fixture_model

        def screenshot(name):
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(output / name), full_page=True, animations="disabled")

        def wait_state(session_id, *states):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                body = request.get(args.base_url + f"/api/v1/sessions/{session_id}").json()
                if body["state"] in states:
                    return body
                time.sleep(0.05)
            raise AssertionError(body)

        def open_review():
            toggle = page.locator(".review-editor-toggle")
            expect(toggle).to_be_visible(timeout=15000)
            if not toggle.evaluate("element => element.open"):
                toggle.locator("summary").first.click()

        def fill_dimensions(rating="1"):
            for field in page.locator("[data-review-dimension]").all():
                field.locator("select").select_option(rating)
                field.locator(".review-evidence-picker summary").click()
                field.locator('input[type="checkbox"]').first.check()
                field.locator("textarea").fill("根据所选原发言逐项核对，仅为本地测试人工观察。")

        def post_revision(session_id, previous, revision, note):
            body = {
                name: previous[name]
                for name in ("report_hash", "rubric_hash", "reviewer_name", "dimensions", "milestones")
            }
            body.update(client_review_id=str(uuid4()), expected_revision=revision, notes=note)
            response = request.post(args.base_url + f"/api/v1/reports/{session_id}/reviews", data=body)
            assert response.status == 201, response.text()
            return response.json()

        try:
            if args.recovery_session_id:
                session_id = args.recovery_session_id
                history = request.get(args.base_url + f"/api/v1/reports/{session_id}/reviews").json()
                latest = max(history["items"], key=lambda item: item["revision"])
                key = f"healteam:review-draft:v1:{session_id}:{history['report_hash']}"
                page.goto(args.base_url + f"/sessions/{session_id}?tab=report", wait_until="networkidle")
                malformed = [
                    {
                        "reviewer_name": "broken",
                        "dimensions": [None],
                        "milestones": [],
                        "base_revision": history["latest_revision"],
                        "pending": None,
                    },
                    {
                        "reviewer_name": "broken",
                        "dimensions": latest["dimensions"],
                        "milestones": latest["milestones"],
                        "base_revision": history["latest_revision"],
                        "pending": None,
                    },
                    {
                        "reviewer_name": "broken",
                        "dimensions": [{**item, "rating": 9} for item in latest["dimensions"]],
                        "milestones": latest["milestones"],
                        "notes": "bad",
                        "base_revision": history["latest_revision"],
                        "pending": None,
                    },
                ]
                for draft in malformed:
                    page.evaluate(
                        "([key, value]) => sessionStorage.setItem(key, JSON.stringify(value))", [key, draft]
                    )
                    page.reload(wait_until="networkidle")
                    open_review()
                    expect(page.get_by_label("复核者姓名或本机标识", exact=True)).to_have_value(
                        latest["reviewer_name"]
                    )
                    expect(page.get_by_label("本次复核备注（可选）", exact=True)).to_have_value(
                        latest["notes"]
                    )
                checks.append("malformed_tab_drafts_fall_back_without_crashing_or_adopting_invalid_ratings")
                page.get_by_label("本次复核备注（可选）", exact=True).fill("GET失败期间仍保留的草稿")
                baseline = history["latest_revision"]
                post_revision(session_id, latest, baseline, "并发新修订，供GET失败恢复测试")
                route_pattern = args.base_url + f"/api/v1/reports/{session_id}/reviews"

                def fail_history(route):
                    if route.request.method == "GET":
                        route.abort("failed")
                    else:
                        route.continue_()

                page.route(route_pattern, fail_history)
                page.get_by_role("button", name=f"保存为修订 {baseline + 1}", exact=True).click()
                rebase = page.get_by_role("button", name="已核对历史，更新保存基准", exact=True)
                expect(rebase).to_be_visible(timeout=15000)
                expect(rebase).to_be_disabled()
                expect(page.get_by_label("本次复核备注（可选）", exact=True)).to_have_value(
                    "GET失败期间仍保留的草稿"
                )
                page.unroute(route_pattern, fail_history)
                page.get_by_role("button", name="重新读取最新修订", exact=True).click()
                expect(rebase).to_be_enabled(timeout=15000)
                rebase.click()
                page.get_by_role("button", name=f"保存为修订 {baseline + 2}", exact=True).click()
                expect(page.locator(".review-message").first).to_contain_text(
                    f"修订 {baseline + 2} 已保存", timeout=15000
                )
                checks.append("conflict_and_failed_latest_get_recover_in_page_without_losing_draft")
                if args.legacy_session_id:
                    before_calls = request.get(args.provider_url.rstrip("/") + "/fixture/health").json()[
                        "calls"
                    ]
                    page.goto(
                        args.base_url + f"/sessions/{args.legacy_session_id}?tab=report",
                        wait_until="networkidle",
                    )
                    expect(page.locator(".ai-review-document")).to_be_visible(timeout=15000)
                    expect(page.locator(".evaluation-section")).to_have_count(0)
                    expect(page.locator(".expert-review")).to_have_count(0)
                    evidence_button = page.locator(".review-evidence button").first
                    seq = int(re.search(r"#(\d+)", evidence_button.inner_text()).group(1))
                    evidence_button.click()
                    expect(page.locator(f'[data-event-seq="{seq}"]')).to_be_focused()
                    assert (
                        request.get(args.provider_url.rstrip("/") + "/fixture/health").json()["calls"]
                        == before_calls
                    )
                    checks.append("legacy_s3_report_and_evidence_open_without_invented_rubric_or_model_calls")
                page.set_viewport_size({"width": 375, "height": 844})
                page.goto(args.base_url + "/calibration", wait_until="networkidle")
                expect(page.locator(".calibration-metrics")).to_contain_text("可用于校准")
                expect(page.locator(".evaluation-scope")).to_contain_text("超出 AI 参考范围")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                screenshot("s4-calibration-mobile.png")
                assert not errors, errors
                result = {
                    "status": "passed",
                    "checks": checks,
                    "page_errors": errors,
                    "external_paid_model_calls": 0,
                }
                (output / "result-recovery.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(json.dumps(result, ensure_ascii=False))
                return
            page.goto(args.base_url + "/calibration", wait_until="networkidle")
            expect(page.get_by_role("heading", name="复核与校准记录", exact=True)).to_be_visible()
            expect(page.locator(".calibration-metrics")).to_be_visible()
            initial = request.get(args.base_url + "/api/v1/calibration").json()
            assert initial["paired_dimensions"] == 0 and initial["exact_agreement"] is None
            expect(page.locator(".calibration-metric").nth(2)).to_contain_text("—")
            checks.append("zero_pairs_show_no_percentage_or_calibration_claim")

            page.goto(args.base_url, wait_until="networkidle")
            page.get_by_label("最大轮次", exact=True).fill("1")
            page.get_by_role("button", name="开始亲自练习").click()
            page.wait_for_url(re.compile(r"/sessions/[0-9a-f-]+"))
            session_id = page.url.split("/sessions/")[1].split("?")[0]
            created.append(session_id)
            page.get_by_label("你的干预", exact=True).fill("请爸爸先说说，你希望今天的对话带来什么？")
            page.get_by_role("button", name="发送干预").click()
            completed = wait_state(session_id, "completed", "failed")
            assert completed["state"] == "completed", completed
            page.get_by_role("tab", name=re.compile("AI 辅助复盘")).click()
            expect(page.get_by_role("heading", name="试行教学量规", exact=True)).to_be_visible(timeout=15000)
            expect(page.locator(".evaluation-dimension")).to_have_count(4)
            expect(page.locator(".evaluation-section")).to_contain_text("不相加为总分")
            before_report = request.get(args.base_url + f"/api/v1/reports/{session_id}").json()
            assert before_report["assessment"] and before_report["rubric"]
            expect(page.locator(".evaluation-scope").first).to_contain_text("实际参考")
            evidence_button = page.locator(".evaluation-dimension .review-evidence button").first
            sequence = int(re.search(r"#(\d+)", evidence_button.inner_text()).group(1))
            evidence_button.click()
            expect(page.locator(f'[data-event-seq="{sequence}"]')).to_be_focused()
            page.get_by_role("tab", name=re.compile("AI 辅助复盘")).click()
            checks.append("trial_rubric_has_four_evidence_linked_dimensions_and_coverage")

            open_review()
            page.get_by_label("复核者姓名或本机标识", exact=True).fill("S4 本地复核者")
            fill_dimensions()
            page.get_by_label("本次复核备注（可选）", exact=True).fill("刷新后应保留的复核草稿")
            page.reload(wait_until="networkidle")
            open_review()
            expect(page.get_by_label("复核者姓名或本机标识", exact=True)).to_have_value("S4 本地复核者")
            expect(page.get_by_label("本次复核备注（可选）", exact=True)).to_have_value(
                "刷新后应保留的复核草稿"
            )
            page.get_by_role("button", name="保存为修订 1", exact=True).click()
            expect(page.locator(".review-message").first).to_contain_text("修订 1 已保存", timeout=15000)
            history = request.get(args.base_url + f"/api/v1/reports/{session_id}/reviews").json()
            assert history["latest_revision"] == 1
            assert request.get(args.base_url + f"/api/v1/reports/{session_id}").json() == before_report
            checks.append("human_review_draft_survives_reload_and_preserves_original_report")

            page.get_by_label("本次复核备注（可选）", exact=True).fill("并发冲突后仍保留的草稿")
            post_revision(session_id, history["items"][0], 1, "另一标签页保存的修订")
            page.get_by_role("button", name="保存为修订 2", exact=True).click()
            expect(page.get_by_role("button", name="已核对历史，更新保存基准", exact=True)).to_be_visible(
                timeout=15000
            )
            expect(page.get_by_label("本次复核备注（可选）", exact=True)).to_have_value(
                "并发冲突后仍保留的草稿"
            )
            page.get_by_role("button", name="已核对历史，更新保存基准", exact=True).click()
            page.get_by_role("button", name="保存为修订 3", exact=True).click()
            expect(page.locator(".review-message").first).to_contain_text("修订 3 已保存", timeout=15000)
            history = request.get(args.base_url + f"/api/v1/reports/{session_id}/reviews").json()
            assert history["latest_revision"] == 3 and len(history["items"]) == 3
            checks.append("revision_conflict_keeps_draft_and_requires_explicit_rebase")

            route_pattern = args.base_url + f"/api/v1/reports/{session_id}/reviews"
            lose_reads = {"value": True}

            def lose_save_response(route):
                if route.request.method == "POST":
                    route.fetch()
                    route.abort("failed")
                elif lose_reads["value"]:
                    route.abort("failed")
                else:
                    route.continue_()

            page.route(route_pattern, lose_save_response)
            page.get_by_label("本次复核备注（可选）", exact=True).fill("响应丢失但只能保存一次")
            page.get_by_role("button", name="保存为修订 4", exact=True).click()
            expect(page.get_by_role("button", name="核对修订保存结果", exact=True)).to_be_visible(
                timeout=15000
            )
            page.reload(wait_until="networkidle")
            lose_reads["value"] = False
            page.unroute(route_pattern, lose_save_response)
            if page.get_by_role("button", name="重新读取复核", exact=True).is_visible():
                page.get_by_role("button", name="重新读取复核", exact=True).click()
            open_review()
            page.get_by_role("button", name="核对修订保存结果", exact=True).click()
            expect(page.locator(".review-message").first).to_contain_text("修订 4 已保存", timeout=15000)
            history = request.get(args.base_url + f"/api/v1/reports/{session_id}/reviews").json()
            assert history["latest_revision"] == len(history["items"]) == 4
            checks.append("accepted_review_response_loss_reconciles_original_request_once")

            page.locator(".review-editor-toggle summary").first.click()
            screenshot("s4-assessment-desktop.png")
            page.goto(args.base_url + "/calibration", wait_until="networkidle")
            summary = request.get(args.base_url + "/api/v1/calibration").json()
            assert summary["reviewed_sessions"] == 1 and summary["paired_dimensions"] == 4
            assert summary["exact_matches"] == 4 and summary["exact_agreement"] == 1
            expect(page.locator(".calibration-metric").nth(0)).to_contain_text("1")
            expect(page.locator(".calibration-metric").nth(2)).to_contain_text("100.0%")
            expect(page.locator(".calibration-main")).to_contain_text("不代表已经完成专家校准")
            with page.expect_download() as download_event:
                page.get_by_role("button", name="导出对照 JSON", exact=True).click()
            export_path = output / "calibration-export.json"
            download_event.value.save_as(export_path)
            exported = export_path.read_text(encoding="utf-8")
            assert "S4 本地复核者" not in exported and "响应丢失但只能保存一次" not in exported
            assert "请爸爸先说说" not in exported and "api_key" not in exported and "base_url" not in exported
            checks.append("latest_revision_only_calibration_and_minimal_export")

            response = request.post(
                args.base_url + "/api/v1/sessions",
                data={
                    "client_request_id": str(uuid4()),
                    "case_id": "teen-truancy-01",
                    "school": "satir",
                    "mode": "demo",
                    "max_rounds": 1,
                },
            )
            demo_id = response.json()["session_id"]
            created.append(demo_id)
            assert wait_state(demo_id, "completed", "failed")["state"] == "completed"
            page.goto(args.base_url + f"/sessions/{demo_id}?tab=report", wait_until="networkidle")
            expect(page.locator(".evaluation-section")).to_contain_text("不代表学员成绩")
            open_review()
            page.get_by_label("复核者姓名或本机标识", exact=True).fill("示范观察测试")
            page.get_by_role("button", name="保存为修订 1", exact=True).click()
            expect(page.locator(".review-message").first).to_contain_text("修订 1 已保存", timeout=15000)
            after_demo = request.get(args.base_url + "/api/v1/calibration").json()
            assert (
                after_demo["paired_dimensions"] == summary["paired_dimensions"]
                and after_demo["excluded_demo_sessions"] >= 1
            )
            checks.append("demo_review_is_observation_and_excluded_from_learner_calibration")

            if args.mock_session_id:
                page.goto(
                    args.base_url + f"/sessions/{args.mock_session_id}?tab=report", wait_until="networkidle"
                )
                expect(page.locator(".evaluation-section")).to_contain_text("未生成 AI 量规判断")
                open_review()
                page.get_by_label("复核者姓名或本机标识", exact=True).fill("离线人工观察")
                page.get_by_role("button", name="保存为修订 1", exact=True).click()
                expect(page.locator(".review-message").first).to_contain_text("修订 1 已保存", timeout=15000)
                after_mock = request.get(args.base_url + "/api/v1/calibration").json()
                assert after_mock["paired_dimensions"] == summary["paired_dimensions"]
                checks.append("mock_report_allows_human_review_without_ai_rating_pairs")

            page.set_viewport_size({"width": 375, "height": 844})
            page.goto(args.base_url + f"/sessions/{session_id}?tab=report", wait_until="networkidle")
            expect(page.locator(".evaluation-section")).to_be_visible()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            screenshot("s4-assessment-mobile.png")
            page.goto(args.base_url + "/calibration", wait_until="networkidle")
            expect(page.locator(".calibration-metrics")).to_be_visible()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            screenshot("s4-calibration-mobile.png")
            checks.append("mobile_assessment_review_and_calibration_have_no_horizontal_overflow")
            assert not errors, errors
            result = {
                "status": "passed",
                "checks": checks,
                "page_errors": errors,
                "external_paid_model_calls": 0,
                "viewport": {"width": 375, "height": 844},
            }
            (output / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            screenshot("failure.png")
            (output / "failure-dom.html").write_text(page.content(), encoding="utf-8")
            raise
        finally:
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
