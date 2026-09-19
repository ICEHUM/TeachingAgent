from __future__ import annotations

FAQ_TASK_KEY = "FAQ-001"
FAQ_VERSION = "v1"

FAQ_STAGES = (
    ("understand_requirements", "理解需求", (
        ("explain_scope", "STUDENT_EXPLANATION", "student_explanation_v1", {"min_length": 20}),
        ("identify_citation_rule", "STATIC_CHECK", "requirement_static_v1", {"field": "citation_rule"}),
    )),
    ("prepare_sources", "准备资料", (
        ("source_manifest", "STATIC_CHECK", "source_manifest_v1", {"min_sources": 3}),
        ("source_quality_review", "TEACHER_REVIEW", "teacher_review_v1", {}),
    )),
    ("implement_retrieval", "实现检索", (
        ("retrieval_public_tests", "AUTO_TEST", "pytest_public_v1", {"suite": "retrieval"}),
        ("retrieval_observation", "STUDENT_EXPLANATION", "student_explanation_v1", {"min_length": 20}),
    )),
    ("generate_cited_answer", "生成带来源回答", (
        ("answer_public_tests", "AUTO_TEST", "pytest_public_v1", {"suite": "answer"}),
        ("citation_static_check", "STATIC_CHECK", "citation_static_v1", {"require_urls": True}),
    )),
    ("validate_boundaries", "边界验证", (
        ("unknown_question_test", "AUTO_TEST", "pytest_public_v1", {"suite": "boundaries"}),
        ("boundary_transfer", "TRANSFER_TASK", "transfer_task_v1", {"case": "unanswerable"}),
    )),
    ("deliver", "交付", (
        ("delivery_static_check", "STATIC_CHECK", "delivery_manifest_v1", {"required_files": ["README.md"]}),
        ("delivery_review", "TEACHER_REVIEW", "teacher_review_v1", {}),
    )),
)

DEFAULT_TASK_POLICY = {
    "failure_threshold": 3,
    "max_help_level": "L2",
    "allow_auto_l2": False,
    "allow_answer_guidance": True,
    "allow_code_patch": False,
    "tool_capabilities": {
        "inspect_runtime": "DIAGNOSTIC",
        "run_public_checks": "EVALUATION",
        "apply_patch": "MODIFICATION",
        "generate_solution": "GENERATION",
        "manage_environment": "ADMIN",
    },
    "assessment_allowed_tools": ["inspect_runtime", "run_public_checks"],
}
