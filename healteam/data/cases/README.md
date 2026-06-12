# data/cases

Therapy case JSON files.

**W1 status:** empty. The `case-content` task (W2) populates this with at
least the MVP primary case (`teen_truancy_01`), plus 2 demo-level cases
(see PRD §11.2).

A case file has the shape (informal — see PRD appendix C):

```json
{
  "case_id": "teen_truancy_01",
  "title": "高一男生拒学，家庭动力分析",
  "family": {
    "father":   { "age": 48, "occupation": "工程师", "trait": "控制、理性化" },
    "mother":   { "age": 45, "occupation": "教师",   "trait": "焦虑、过度保护" },
    "patient":  { "age": 16, "gender": "male",      "trait": "回避、退行" }
  },
  "presenting_problem": "拒学 3 个月，与父母冲突频繁",
  "family_pattern": "父母联盟对抗病人，病人退行",
  "treatment_focus": "重建代际边界，让病人承担年龄责任"
}
```

The `load_case` node in `packages/agents/orchestrator.py` will hydrate
`Session` from these files in W2.
