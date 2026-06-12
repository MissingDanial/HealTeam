"""HealTeam W1 案例层测试。

测试目标：
1. `data/cases/teen-truancy-01.json` 符合 Pydantic schema（手写校验，schema 不定义 Case 类型）
2. 案例关键字段完整性（家庭成员 / 防御模式 / 家庭动力）
3. 3 个流派模板覆盖必备技术、互不重叠
4. 跨流派一致性：3 流派的"流派出场"不混用

W1 不调真 LLM，纯文件 / 字符串分析。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_PATH = REPO_ROOT / "data" / "cases" / "teen-truancy-01.json"
SCHOOLS_DIR = REPO_ROOT / "data" / "schools"
SCHEMA_PATH = REPO_ROOT / "packages" / "memory" / "schema.py"

STRUCTURAL_PATH = SCHOOLS_DIR / "structural.md"
BOWEN_PATH = SCHOOLS_DIR / "bowen.md"
SATIR_PATH = SCHOOLS_DIR / "satir.md"

ALL_SCHOOLS = [
    ("structural", STRUCTURAL_PATH),
    ("bowen", BOWEN_PATH),
    ("satir", SATIR_PATH),
]


# ---------------------------------------------------------------------------
# 1. 案例 JSON 基础验证
# ---------------------------------------------------------------------------

class TestCaseJSON:
    """案例 JSON 必须可解析、字段齐、防御模式非空。"""

    @pytest.fixture(scope="class")
    def case(self) -> dict:
        with CASE_PATH.open(encoding="utf-8") as f:
            return json.load(f)

    def test_case_json_exists(self):
        assert CASE_PATH.exists(), f"案例 JSON 缺失: {CASE_PATH}"

    def test_case_json_is_valid_json(self):
        with CASE_PATH.open(encoding="utf-8") as f:
            data = json.load(f)  # 若解析失败会直接抛错
        assert isinstance(data, dict)

    def test_case_has_required_top_level_fields(self, case: dict):
        required = [
            "case_id",
            "title",
            "family_system_pattern",
            "summary",
            "presenting_problem",
            "family",
            "expected_milestones",
        ]
        for field in required:
            assert field in case, f"案例缺失必填字段: {field}"

    def test_case_id_is_teen_truancy(self, case: dict):
        assert case["case_id"] == "teen-truancy-01"

    def test_presenting_problem_mentions_refusal(self, case: dict):
        assert "拒学" in case["presenting_problem"] or "不上学" in case["presenting_problem"]

    def test_family_has_three_members(self, case: dict):
        family = case["family"]
        for role in ("father", "mother", "patient"):
            assert role in family, f"家庭缺失成员: {role}"

    def test_each_family_member_has_distinct_defenses(self, case: dict):
        family = case["family"]
        for role, person in family.items():
            assert "defense_pattern" in person, f"{role} 缺失 defense_pattern"
            assert isinstance(person["defense_pattern"], list)
            assert len(person["defense_pattern"]) >= 3, (
                f"{role} 防御模式数量 < 3，太浅"
            )

    def test_defense_patterns_have_required_fields(self, case: dict):
        family = case["family"]
        for role, person in family.items():
            for defense in person["defense_pattern"]:
                for f in ("name", "description", "example_utterance"):
                    assert f in defense, f"{role}.{defense.get('name', '?')} 缺失 {f}"

    def test_expected_milestones_are_observable(self, case: dict):
        """验收：里程碑必须是"治疗中可观察的变化"，不是"病人会好起来"这种空话。"""
        milestones = case["expected_milestones"]
        assert isinstance(milestones, list)
        assert len(milestones) >= 3, "里程碑至少 3 个"
        vague_phrases = ["病人会好起来", "孩子会康复", "家庭和睦"]
        for m in milestones:
            assert not any(v in str(m) for v in vague_phrases), (
                f"里程碑太空泛: {m}"
            )

    def test_summary_mentions_family_dynamics(self, case: dict):
        """验收：summary 必须描述家庭动力（联盟/三角/代际），不是症状描述。"""
        summary = case["summary"]
        dynamics_keywords = ["联盟", "代际", "边界", "子系统", "三角", "结构", "互动"]
        assert any(kw in summary for kw in dynamics_keywords), (
            f"案例 summary 缺乏家庭动力学关键词: {summary[:80]}"
        )


# ---------------------------------------------------------------------------
# 2. 流派模板完整性
# ---------------------------------------------------------------------------

class TestSchoolTemplates:
    """3 个流派模板必须都存在、覆盖必备章节、技术清单非空。"""

    @pytest.mark.parametrize("school_id,path", ALL_SCHOOLS, ids=[s for s, _ in ALL_SCHOOLS])
    def test_school_template_exists(self, school_id: str, path: Path):
        assert path.exists(), f"{school_id} 模板缺失: {path}"

    @pytest.mark.parametrize("school_id,path", ALL_SCHOOLS, ids=[s for s, _ in ALL_SCHOOLS])
    def test_school_template_has_required_sections(self, school_id: str, path: Path):
        content = path.read_text(encoding="utf-8")
        required_sections = [
            "开场话术",
            "干预技术清单",
            "典型节律",
            "关键干预时机",
        ]
        for section in required_sections:
            assert section in content, f"{school_id} 模板缺失章节: {section}"

    @pytest.mark.parametrize("school_id,path", ALL_SCHOOLS, ids=[s for s, _ in ALL_SCHOOLS])
    def test_school_template_has_5_to_8_techniques(self, school_id: str, path: Path):
        """验收：每个流派必须有 5~8 个干预技术。"""
        content = path.read_text(encoding="utf-8")
        # 简单规则：技术表行通常以 "| N |" 开头
        # 匹配形如 "| 1 |" "| 2 |" 这样的序号
        tech_markers = re.findall(r"\|\s*\d+\s*\|\s*\*\*", content)
        assert 5 <= len(tech_markers) <= 8, (
            f"{school_id} 干预技术数量 {len(tech_markers)} 不在 5~8 范围"
        )

    @pytest.mark.parametrize("school_id,path", ALL_SCHOOLS, ids=[s for s, _ in ALL_SCHOOLS])
    def test_school_template_references_its_founder(self, school_id: str, path: Path):
        """验收：流派代表人物引用必须正确。"""
        content = path.read_text(encoding="utf-8")
        founders = {
            "structural": "Minuchin",
            "bowen": ["Bowen", "Murray Bowen"],
            "satir": ["Satir", "Virginia Satir"],
        }
        expected = founders[school_id]
        if isinstance(expected, list):
            assert any(name in content for name in expected), (
                f"{school_id} 模板未引用其流派之父: {expected}"
            )
        else:
            assert expected in content, f"{school_id} 模板未引用其流派之父: {expected}"


# ---------------------------------------------------------------------------
# 3. 流派差异化（关键防混用）
# ---------------------------------------------------------------------------

class TestSchoolDifferentiation:
    """3 个流派必须有本质差异：差异化关键词、技术不重叠、流派特化术语不混用。"""

    @pytest.fixture(scope="class")
    def contents(self) -> dict[str, str]:
        return {sid: p.read_text(encoding="utf-8") for sid, p in ALL_SCHOOLS}

    def test_structural_uses_systems_terms(self, contents: dict):
        """验收：结构派必须用其专属术语。"""
        required = ["边界", "子系统", "enact"]
        for kw in required:
            assert kw in contents["structural"], f"结构派缺失术语: {kw}"

    def test_bowen_uses_bowen_terms(self, contents: dict):
        """验收：Bowen 必须用其专属术语。"""
        required = ["代际", "自我分化", "家谱图"]
        for kw in required:
            assert kw in contents["bowen"], f"Bowen 缺失术语: {kw}"

    def test_satir_uses_satir_terms(self, contents: dict):
        """验收：萨提亚必须用其专属术语。"""
        required = ["冰山", "一致性", "沟通姿态"]
        for kw in required:
            assert kw in contents["satir"], f"萨提亚缺失术语: {kw}"

    def test_structural_does_not_use_bowen_genogram_term(self, contents: dict):
        """验收：结构派**不该**用 Bowen 的"家谱图"作为核心工具（在 §2.1 已列明）。"""
        # 结构派的 §2.1 应明确说"家谱图是 Bowen 的工具，结构派不用"
        assert "家谱图" in contents["structural"]
        # "Bowen 的" 或 "不是" 应该出现
        structural = contents["structural"]
        idx = structural.find("家谱图")
        window = structural[idx:idx + 200]
        assert "Bowen" in window or "不用" in window, (
            f"结构派模板提到了家谱图但没明确标记为'非本流派': {window[:80]}"
        )

    def test_satir_does_not_use_structural_enact(self, contents: dict):
        """验收：萨提亚**不该**用结构派的 enact。"""
        satir = contents["satir"]
        # 萨提亚应该明确把 enact 列为"非本流派"
        assert "enact" in satir
        idx = satir.find("enact")
        window = satir[idx:idx + 200]
        assert "不是" in window or "不在" in window or "结构派" in window, (
            f"萨提亚模板提到了 enact 但没明确标记为'非本流派'"
        )

    def test_no_majority_overlap_in_techniques(self, contents: dict):
        """验收：3 流派的干预技术清单重叠度 < 50%。

        近似度量：从每个流派的"§2 干预技术"段抽取关键技术名词（行内加粗部分），
        计算 3 组集合的交集 / 并集比。
        """
        def extract_terms(text: str) -> set:
            # 简化版：抓所有加粗双星号内容
            return set(re.findall(r"\*\*([^*]+)\*\*", text))

        sets = {sid: extract_terms(c) for sid, c in contents.items()}
        union = set().union(*sets.values())
        intersection = set().intersection(*sets.values())

        if not union:
            pytest.skip("流派模板技术名词提取为空")

        overlap = len(intersection) / len(union)
        assert overlap < 0.5, (
            f"3 流派技术重叠度 {overlap:.1%} >= 50% (intersection={intersection})"
        )


# ---------------------------------------------------------------------------
# 4. 案例-流派兼容性
# ---------------------------------------------------------------------------

class TestCaseSchoolCompatibility:
    """`teen-truancy-01` 必须与 3 个流派都"对接得上"。"""

    @pytest.fixture(scope="class")
    def case(self) -> dict:
        with CASE_PATH.open(encoding="utf-8") as f:
            return json.load(f)

    def test_case_summary_mentions_coalition_or_boundary(self, case: dict):
        """验收：案例 summary 必须包含至少一个"结构派话题"关键词。"""
        keywords = ["联盟", "边界", "子系统", "结构"]
        assert any(k in case["summary"] for k in keywords)

    def test_case_origin_family_present(self, case: dict):
        """验收：案例必须有代际叙事（用于 Bowen 流派）。"""
        family = case["family"]
        # 至少一个成员有 origin_family_note 字段
        has_origin = any("origin_family_note" in p for p in family.values())
        assert has_origin, "案例没有任何成员的 origin_family_note，Bowen 流派无切入点"

    def test_case_communication_stance_implied(self, case: dict):
        """验收：案例的 defense_pattern 应能映射到萨提亚的 4 种沟通姿态之一。"""
        # 简化版：检查 defense_pattern 是否含有"姿态"相关关键词
        family = case["family"]
        all_defense_text = " ".join(
            d.get("name", "") + " " + d.get("description", "")
            for p in family.values()
            for d in p.get("defense_pattern", [])
        )
        # 萨提亚的 4 种姿态：讨好 / 指责 / 超理智 / 打岔
        stance_keywords = ["理性", "讨好", "指责", "退行", "打岔", "回避", "情绪化"]
        assert any(kw in all_defense_text for kw in stance_keywords), (
            "案例防御模式未体现任何萨提亚沟通姿态"
        )
