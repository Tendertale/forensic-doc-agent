# W5 测试交付报告（`backend/tests/**`）

任务：按 `docs/IMPLEMENTATION_PLAN.md` §3.2 / §4 与 `docs/requirements.md` 编号写 pytest 测试。
归属：W5 子智能体 F。**只创建/修改 `backend/tests/**`**；未改动 `backend/app/**`、`backend/scripts/**`、
`frontend/**`、`docs/**`、`pytest.ini`、`constants.py`、`schemas.py`。

---

## 1. 文件清单与用例数

| 文件 | 用例数 | 覆盖主题 |
| --- | --- | --- |
| `backend/tests/conftest.py` | —（fixture） | `tmp_path` 隔离的 `app` / `client` / `db` / `storage` / `storage_root` / `seeded_case` |
| `backend/tests/helpers.py` | —（辅助） | 纯色占位图、sidecar 扫描件装置、阶段调用封装、docx 结构检查 |
| `backend/tests/test_terminology.py` | 27 | R-4/R-23/R-30～R-37、N-2～N-5 |
| `backend/tests/test_extractor.py` | 24 | R-2/R-3/R-15～R-17/R-21/R-22/R-36/R-44、N-2～N-7、INV-1 |
| `backend/tests/test_checker.py` | 38 | §5.7 全部 23 个 code（19 旧 + 4 `PHOTO_*`）、去重、顺序 |
| `backend/tests/test_api_cases.py` | 27 | 建案/详情/分页筛选/乐观锁/阶段机/404/删除/N-10 |
| `backend/tests/test_api_pipeline.py` | 21 | 全链路、docx 产物、阻断、原件只读、PDF、成像探测 |
| `backend/tests/test_api_photos.py` | 17 | R-54～R-58、N-11/N-12、INV-11 |
| `backend/tests/test_api_corrections.py` | 14 | R-7/R-38～R-41、INV-6～INV-8 |
| `backend/tests/test_workflow.py` | 13 | 阶段推进/回退、幂等、`BlockedByChecklist`、`llm` MockTransport |
| `backend/tests/test_guards.py` | 9 | R-1/R-58/N-5/N-9/N-11/N-12 的静态与常量护栏、密钥不落库 |
| **合计** | **190** | |

隔离与离线：数据库与存储根一律 `tmp_path`；OCR 只用离线 `sidecar`；`llm` 只用
`httpx.MockTransport`；测试照片一律 Pillow 纯色占位图，测试代码只读文件路径与像素宽高（N-11）。
实测：跑完整套后仓库 `storage/`、`data/` 的最大修改时间**未变化**（未被测试写入）。

## 2. `python -m pytest` 真实输出

```
183 passed, 7 xfailed, 1 warning in 21.03s
190 tests collected in 0.16s
```

- `filterwarnings` 忽略 `DeprecationWarning`；唯一 warning 是 starlette 自带的
  `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`（与本次改动无关）。
- 收集到的用例**全部来自 `backend/tests/`**：`python -m pytest --collect-only -q` 中
  `_ref_hotspot` 匹配数为 **0**。
- 7 个 `xfail`＝**5 处实现缺陷**（见 §4）。断言按需求/契约原文书写且完整保留，
  未放宽、未改成恒真；缺陷修复后这些用例会自动转为 XPASS。

## 3. R-/N- 覆盖矩阵

（用例名省略 `backend/tests/` 前缀；`test_checker.py[...]` 指参数化 id。）

### 目标与工作范围

| 编号 | 用例 |
| --- | --- |
| R-1 | `test_guards.py::test_r1_workbench_root_exposes_the_four_step_flow` |
| R-2 | `test_extractor.py::test_r2_commission_document_fields_extracted_from_printed_blocks`、`::test_r2_r22_only_fields_with_sources_are_written`、`test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist` |
| R-3 | `test_extractor.py::test_r3_injury_seq_follows_page_and_block_order`、`::test_multi_column_single_line_is_split_per_column`、`test_terminology.py::test_r3_normalize_case_orders_fields_then_injuries_by_seq` |
| R-4 | `test_terminology.py::test_rules_are_ordered_by_priority_then_code_and_first_match_wins`、`::test_rules_with_equal_priority_are_ordered_by_code`、`::test_invalid_regex_rule_is_skipped_without_breaking_normalization`、`::test_compile_rules_skips_rows_without_code_or_pattern`、`::test_terminology_rules_seed_import_and_crud`、`::test_named_rule_saved_to_db_is_applied_by_normalize_endpoint` |
| R-5 | `test_api_pipeline.py::test_r5_generated_docx_opens_with_python_docx_and_has_no_placeholders`、`::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist` |
| R-6 | `test_checker.py::test_checker_code_table_matches_documentation[*]`（23 参数）、`::test_check_case_returns_items_in_fixed_category_order`、`test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist` |
| R-7 | `test_api_corrections.py::test_r7_recognition_correction_updates_confirmed_value_and_keeps_raw_text`、`::test_r7_injuries_can_be_reordered_and_order_is_persisted` |
| R-8 | `test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist`（基础信息/案情/尸表所见/损伤/照片栏目全在产物中） |
| R-9 | `test_api_cases.py::test_r14_r15_extracted_fields_carry_group_and_full_source_trace`、`test_api_pipeline.py::test_r19_r24_n8_render_and_preprocess_never_touch_originals` |
| R-10 | `test_workflow.py::test_stage_advances_one_step_per_executed_stage`、`::test_llm_extract_only_accepts_values_backed_by_a_source_block`、`::test_llm_verify_key_maps_status_codes_and_requires_a_key`、`test_api_cases.py::test_r11_case_detail_exposes_every_aggregate_section` |
| R-11 | `test_api_cases.py::test_r11_case_detail_exposes_every_aggregate_section`、`::test_ocr_blocks_endpoint_is_paginated_and_scoped_to_case` |
| R-12 | `test_api_cases.py::test_r12_r13_config_reports_material_and_ocr_contract`、`test_api_pipeline.py::test_post_materials_accepts_expected_version_from_request_body`（xfail）、`::test_post_materials_accepts_expected_version_as_query_parameter`（xfail） |
| R-13 | `test_api_cases.py::test_r12_r13_config_reports_material_and_ocr_contract`、`::test_material_file_variant_validation` |
| R-14 | `test_api_cases.py::test_r14_r15_extracted_fields_carry_group_and_full_source_trace`、`test_extractor.py::test_inv1_field_group_and_label_come_from_the_field_index` |
| R-15 | `test_extractor.py::test_r15_every_field_keeps_full_source_trace`、`test_terminology.py::test_r15_normalize_field_prefers_confirmed_value_over_raw_text`、`test_api_cases.py::test_r14_r15_extracted_fields_carry_group_and_full_source_trace` |
| R-16 | `test_extractor.py::test_r16_one_ocr_line_yields_at_most_one_injury_and_two_lines_stay_two`、`::test_r16_negated_or_blank_lines_do_not_become_injuries` |
| R-17 | `test_extractor.py::test_r17_blank_or_placeholder_marks_are_not_recorded`、`::test_r17_not_examined_before_negative_and_recorded`、`::test_r17_infer_status_never_returns_unclear`、`::test_r17_status_set_matches_contract_enumeration`、`test_api_cases.py::test_r17_field_status_dictionary_is_exposed_by_config` |
| R-18 | `test_api_pipeline.py::test_r18_imaging_probe_distinguishes_text_pdf_from_scanned_pdf`、`test_api_cases.py::test_material_file_variant_validation` |
| R-19 | `test_api_pipeline.py::test_r19_r24_n8_render_and_preprocess_never_touch_originals`、`test_api_photos.py::test_photo_file_endpoint_returns_the_original_image_bytes` |
| R-20 | `test_api_cases.py::test_config_reports_engine_status_and_todo_downloads`、`test_api_pipeline.py::test_paddle_unavailable_returns_409_with_todo_dl_2`（xfail） |
| R-21 | `test_extractor.py::test_r21_low_confidence_injury_line_keeps_a_review_note`、`::test_r21_low_confidence_field_becomes_unclear`、`test_checker.py::test_r21_recognition_check_reports_low_confidence_and_unreferenced_handwriting`、`test_api_pipeline.py::test_r21_low_confidence_field_enters_the_checklist_as_warning` |
| R-22 | `test_extractor.py::test_r2_r22_only_fields_with_sources_are_written`、`test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist`、`test_checker.py::test_checker_code_table_matches_documentation[FIELD_SOURCE_MISSING]` |
| R-23 | `test_terminology.py::test_r23_unclear_status_keeps_raw_text_and_asks_confirmation`、`::test_no_rule_hit_keeps_raw_text_and_requires_confirmation` |
| R-24 | `test_api_pipeline.py::test_r19_r24_n8_render_and_preprocess_never_touch_originals`、`::test_docx_engine_status_reports_builtin_fallback_and_todo_dl_1` |
| R-25 | `test_api_pipeline.py::test_r25_pdf_export_produces_a_real_file`（LibreOffice 缺失时 `pytest.skip`；本机已安装，实跑通过） |
| R-26 | `test_checker.py::test_check_case_returns_items_in_fixed_category_order`、`::test_checker_code_table_matches_documentation[*]`、`test_workflow.py::test_run_check_is_idempotent_and_keeps_handled_items` |
| R-27 | `test_api_pipeline.py::test_r27_internal_draft_is_allowed_and_marked_with_draft_marker` |
| R-28 | `test_api_pipeline.py::test_inv5_r28_pending_issue_is_blocked_by_open_blocking_items`、`::test_waived_blocking_items_unblock_pending_issue_generation`、`test_workflow.py::test_run_render_pending_issue_raises_blocked_by_checklist`、`test_checker.py::test_r28_has_blocking_helper_只认_blocking` |
| R-29 | **无自动化用例**：人工程序（复核/签名/盖章）不可自动化；矩阵标注 N/A |

### 简写规则

| 编号 | 用例 |
| --- | --- |
| R-30 | `test_terminology.py::test_r30_body_length_with_unit_expands_without_changing_value` |
| R-31 | `test_terminology.py::test_r31_injury_measure_expands_without_adding_facts`、`::test_r31_normalize_injury_assembles_sentence_from_structured_fields` |
| R-32 | `test_terminology.py::test_r32_livor_column_expands_without_inferring_death_time` |
| R-33 | `test_terminology.py::test_r33_body_length_missing_unit_keeps_value_and_asks_confirmation`、`test_extractor.py::test_r33_unit_is_not_invented_at_extraction_time`、`test_api_pipeline.py::test_r33_missing_unit_keeps_value_through_the_api` |
| R-34 | `test_terminology.py::test_r34_ambiguous_shorthand_only_asks_for_confirmation` |
| R-35 | `test_terminology.py::test_r35_n5_blank_is_recorded_as_not_recorded_only`、`::test_r35_blank_clothing_column_from_record_book_is_not_recorded`、`test_api_pipeline.py::test_n5_blank_clothing_column_is_written_as_not_recorded` |
| R-36 | `test_extractor.py::test_r36_single_line_holding_two_measurements_is_not_split_or_mixed` |
| R-37 | `test_terminology.py::test_r37_same_shorthand_outside_its_column_is_not_expanded` |

### 修正工作流

| 编号 | 用例 |
| --- | --- |
| R-38 | `test_api_corrections.py::test_r38_correction_record_keeps_before_after_reason_confirmer_and_scope`、`::test_correction_requires_a_real_change_and_a_reason`、`::test_r38_workflow_order_correction_is_recorded` |
| R-39 | `test_api_corrections.py::test_inv7_scope_outside_case_requires_scope_value`、`::test_r39_non_case_only_scope_is_recorded_and_does_not_leak_to_other_cases` |
| R-40 | `test_api_corrections.py::test_r40_fact_correction_supersedes_documents_and_reopens_field_review`、`::test_r41_format_correction_keeps_fact_numbers`、`test_workflow.py::test_upsert_fields_keeps_confirmed_rows_on_reextract` |
| R-41 | `test_api_corrections.py::test_r41_approved_version_is_kept_and_new_version_supersedes_it`、`::test_inv8_deliver_requires_an_approved_document` |

### 办公文书与验收

| 编号 | 用例 |
| --- | --- |
| R-42 | `test_api_pipeline.py::test_r5_generated_docx_opens_with_python_docx_and_has_no_placeholders`（标题 18pt、表格保留） |
| R-43 | `test_extractor.py::test_r15_every_field_keeps_full_source_trace`、`test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist` |
| R-44 | `test_extractor.py::test_r44_same_field_with_two_different_sources_is_explicit_conflict`、`::test_r44_same_value_from_two_sources_is_not_a_conflict`、`test_checker.py::test_r44_field_conflict_is_blocking_and_lists_all_sources` |
| R-45 | `test_checker.py::test_inv2_critical_pending_or_unclear_field_is_blocking`、`test_api_pipeline.py::test_r5_r6_full_pipeline_from_materials_to_docx_and_checklist`（`FIELD_UNRESOLVED_CRITICAL`） |
| R-46 | `test_api_pipeline.py::test_r27_internal_draft_is_allowed_and_marked_with_draft_marker`、`::test_r46_pending_issue_body_has_no_draft_marker`（xfail）、`test_api_corrections.py::test_waived_checklist_item_requires_a_reason`、`test_checker.py::test_checker_code_table_matches_documentation[DOCUMENT_DRAFT_MARKER]` |
| R-47 | `test_api_cases.py::test_n10_r47_cases_do_not_share_fields_materials_or_corrections`、`::test_n10_photo_slots_are_scoped_to_the_case` |
| R-48 | `test_api_cases.py::test_r48_case_no_is_auto_generated_and_stored_with_intake_event`、`::test_r48_stage_change_is_written_to_the_audit_timeline`、`test_workflow.py::test_stage_advances_one_step_per_executed_stage` |
| R-49 | **无自动化用例**：GA/T 147—2019 的采用与版本由用户确认后写入规则配置，代码中无该字面量 |
| R-50 | `test_api_pipeline.py::test_r25_pdf_export_produces_a_real_file`、`test_api_corrections.py::test_inv8_deliver_requires_an_approved_document` |
| R-51 | `test_api_pipeline.py::test_r51_download_filename_follows_case_no_doc_type_version` |
| R-52 | `test_checker.py::test_check_case_returns_items_in_fixed_category_order`（五类）、`test_api_pipeline.py::test_r25_pdf_export_produces_a_real_file`、`test_guards.py::test_r52_acceptance_notes_are_documented_as_todo_downloads`（实际耗时项不可自动化） |
| R-53 | `test_guards.py::test_r52_acceptance_notes_are_documented_as_todo_downloads`（TODO-DL-6/7 记账）；实样验收不在自动化范围 |

### 部位照片

| 编号 | 用例 |
| --- | --- |
| R-54 | `test_api_photos.py::test_r54_photo_is_registered_as_file_plus_position_metadata_only`、`::test_r54_placement_can_be_assigned_and_changed_through_the_api`、`::test_r54_illegal_position_is_422_and_non_photo_material_is_400`、`::test_r54_photo_position_schema_contract_is_frozen`、`::test_r54_photo_slots_follow_the_catalog_order_and_shape` |
| R-55 | `test_api_photos.py::test_n11_photos_are_skipped_by_ocr_and_keep_zero_blocks`、`::test_inv11_photo_ocr_blocks_trip_the_blocking_check_item`（xfail）、`test_checker.py::test_n11_photo_content_read_is_blocking_when_photo_material_has_ocr_blocks`、`test_api_pipeline.py::test_n11_run_ocr_skips_photos_and_photos_never_get_ocr_blocks`、`::test_n11_photo_only_case_yields_no_fields_or_injuries` |
| R-56 | `test_api_photos.py::test_r56_rendered_docx_holds_one_picture_per_assigned_and_existing_photo`、`::test_r56_pictures_sit_after_their_own_slot_heading`、`::test_photo_width_respects_column_limit_and_keeps_aspect_ratio`、`::test_photo_file_missing_warning_and_no_substitution` |
| R-57 | `test_api_photos.py::test_r57_unassigned_photo_is_absent_from_every_slot`、`::test_r57_unassigned_photo_raises_position_missing_warning`、`test_checker.py::test_r57_unassigned_photo_produces_warning_pointing_at_material` |
| R-58 | `test_guards.py::test_r58_workbench_declares_photo_position_entry_and_no_reading_policy`（Vue 源码的浏览器主流程属 TODO-DL-3，未构建验证） |

### 红线 `N-*`

| 编号 | 用例 |
| --- | --- |
| N-1 | `test_api_cases.py::test_n1_opinion_text_is_only_written_by_user_request`、`test_checker.py::test_checker_code_table_matches_documentation[OPINION_FROM_SOURCE]`、`test_api_photos.py::test_n1_opinion_copied_from_ocr_text_is_blocked_by_check`（xfail）、`test_workflow.py::test_llm_extract_only_accepts_values_backed_by_a_source_block` |
| N-2 | `test_terminology.py::test_n2_normalize_injury_never_adds_missing_unit_or_size`、`::test_n2_injury_without_type_keeps_raw_text`、`test_extractor.py::test_r16_negated_or_blank_lines_do_not_become_injuries`、`::test_n3_n4_injury_keeps_original_numbers_side_and_unit`、`test_workflow.py::test_apply_normalization_keeps_fact_columns_and_forces_not_recorded` |
| N-3 | `test_terminology.py::test_n3_number_precision_is_never_manufactured`、`::test_n3_measure_keeps_original_numbers_verbatim`、`test_extractor.py::test_n3_n4_injury_keeps_original_numbers_side_and_unit`、`test_api_pipeline.py::test_r33_missing_unit_keeps_value_through_the_api` |
| N-4 | `test_terminology.py::test_n4_negative_keeps_negation_words_in_output`、`::test_n4_guard_reverts_when_rule_would_drop_negation`、`test_extractor.py::test_n3_n4_injury_keeps_original_numbers_side_and_unit`、`test_checker.py::test_checker_code_table_matches_documentation[NEGATION_UNPRESERVED]` |
| N-5 | `test_terminology.py::test_r35_n5_blank_is_recorded_as_not_recorded_only`、`::test_n5_not_recorded_wins_even_when_source_says_otherwise`、`test_extractor.py::test_empty_column_value_is_not_recorded_not_negative`、`test_api_pipeline.py::test_n5_blank_clothing_column_is_written_as_not_recorded`、`test_checker.py::test_n5_not_recorded_mislabeled_is_blocking`、`test_guards.py::test_n5_workbench_never_presents_a_blank_column_as_normal` |
| N-6 | `test_extractor.py::test_n6_case_summary_keeps_commission_group_not_general_findings` |
| N-7 | `test_extractor.py::test_n7_name_is_not_written_into_commission_unit_and_dates_stay_separate` |
| N-8 | `test_api_pipeline.py::test_r19_r24_n8_render_and_preprocess_never_touch_originals`、`test_api_cases.py::test_material_file_variant_validation` |
| N-9 | `test_guards.py::test_n9_backend_never_dynamically_executes_content`、`::test_n9_subprocess_is_confined_to_libreoffice_export`、`test_api_pipeline.py::test_n9_material_bytes_are_stored_verbatim_and_only_as_data` |
| N-10 | `test_api_cases.py::test_n10_r47_cases_do_not_share_fields_materials_or_corrections`、`::test_n10_photo_slots_are_scoped_to_the_case`、`test_api_photos.py::test_r54_photo_is_registered_as_file_plus_position_metadata_only` |
| N-11 | `test_guards.py::test_n11_constants_keep_photos_out_of_every_ocr_path`、`test_api_photos.py::test_n11_photos_are_skipped_by_ocr_and_keep_zero_blocks`、`::test_inv11_photo_ocr_blocks_trip_the_blocking_check_item`（xfail）、`test_checker.py::test_n11_photo_content_read_is_blocking_when_photo_material_has_ocr_blocks`、`::test_n11_photo_codes_absent_when_photos_are_clean_and_assigned`、`test_api_pipeline.py::test_n11_run_ocr_skips_photos_and_photos_never_get_ocr_blocks`、`::test_n11_photo_only_case_yields_no_fields_or_injuries`、`test_api_cases.py::test_r12_r13_config_reports_material_and_ocr_contract` |
| N-12 | `test_api_photos.py::test_n12_empty_slots_keep_theirs_heading_and_write_placeholder`、`::test_photo_file_missing_warning_and_no_substitution`、`::test_photo_slot_empty_items_are_info_for_declared_but_unfilled_slots`、`test_checker.py::test_n12_photo_slot_empty_is_info_and_never_says_normal`、`::test_photo_file_missing_flag_only_for_provably_absent_files`、`test_guards.py::test_n12_placeholder_text_is_explicit_and_not_a_normal_finding` |

### 不变量与契约

| 编号 | 用例 |
| --- | --- |
| INV-1 | `test_extractor.py::test_inv1_field_group_and_label_come_from_the_field_index`、`test_api_cases.py::test_r14_r15_extracted_fields_carry_group_and_full_source_trace` |
| INV-2 | `test_checker.py::test_inv2_critical_pending_or_unclear_field_is_blocking` |
| INV-3 | `test_terminology.py::test_n5_not_recorded_wins_even_when_source_says_otherwise`、`test_workflow.py::test_apply_normalization_keeps_fact_columns_and_forces_not_recorded` |
| INV-4 | `test_extractor.py::test_r16_one_ocr_line_yields_at_most_one_injury_and_two_lines_stay_two`、`::test_r36_single_line_holding_two_measurements_is_not_split_or_mixed` |
| INV-5 | `test_workflow.py::test_run_render_pending_issue_raises_blocked_by_checklist`、`test_api_pipeline.py::test_inv5_r28_pending_issue_is_blocked_by_open_blocking_items` |
| INV-6 | `test_api_corrections.py::test_r40_fact_correction_supersedes_documents_and_reopens_field_review`、`::test_r41_approved_version_is_kept_and_new_version_supersedes_it` |
| INV-7 | `test_api_corrections.py::test_inv7_scope_outside_case_requires_scope_value` |
| INV-8 | `test_api_corrections.py::test_inv8_deliver_requires_an_approved_document`、`test_workflow.py::test_deliver_without_any_document_raises_invalid_transition` |
| INV-9 | `test_api_cases.py::test_inv9_case_update_without_expected_version_is_422`、`::test_inv9_stale_expected_version_returns_409`、`test_workflow.py::test_inv9_internal_writes_with_zero_version_do_not_check_the_lock` |
| INV-10 | `test_api_cases.py::test_n1_opinion_text_is_only_written_by_user_request`、`test_api_cases.py::test_n10_r47_cases_do_not_share_fields_materials_or_corrections` |
| INV-11 | `test_api_photos.py::test_n11_photos_are_skipped_by_ocr_and_keep_zero_blocks`、`test_checker.py::test_n11_photo_content_read_is_blocking_when_photo_material_has_ocr_blocks` |

### 未覆盖 / 不可自动化（明确记账）

- **R-29**：与司法鉴定复核、签名、盖章等人工程序衔接——非软件行为，无自动化用例。
- **R-49**：GA/T 147—2019 的采用与版本由用户确认；代码与产物中无该标准字面量，无对应用例。
- **R-40 后半句**（“只改字号或间距时可以只重新检查排版”）：当前实现没有“仅排版修正”的入口
  （`corrections` 的 `format` 也走字段级失效），未用例化。
- **R-52 的“实际效率”**、**R-53 的实样验收**、**TODO-DL-6/7**：需外部样本，仅以配置/文档用例兜底。
- **R-58 的 Vue 源码浏览器验证**、**TODO-DL-3**：本机无法离线安装 npm 依赖，只做源码级静态核对。
- **R-18 的数据库落库路径**（`material_pages.is_scanned/has_text_layer` 经 `register_pages`）：
  已覆盖 `imaging.probe` 的类型判定，未通过 HTTP 触发落库路径。
- **N-11 的“不得调用视觉模型”**：以常量护栏（`OCR_MATERIAL_KINDS` 不含 photo）、
  `run_ocr` 跳过行为、照片 `ocr_blocks` 恒 0 与 `PHOTO_CONTENT_READ` 用例共同覆盖；
  代码库中不存在视觉模型调用路径（`llm` 只用 MockTransport 测过）。

## 4. 发现的实现缺陷（5 处，7 个 xfail 用例）

> 复现命令统一形如
> `python -m pytest "backend/tests/<file>::<case>" -q -rxX --tb=short`。

### D1（blocking）`POST /api/cases/{id}/materials` 恒 422

- 现象：任何形式的材料登记都返回 `422 {"detail":"expected_version Field required"}`。
- 根因：`backend/app/routes.py:545-547` 先从请求体 `pop("expected_version")`，
  再用 `_payload(MaterialCreate, raw)` 校验；而 `backend/app/schemas.py:158`
  的 `MaterialCreate.expected_version: int = Field(ge=1)` 是**必填**字段 → 校验必然失败。
  查询参数路径同样被同一模型挡住（`backend/app/routes.py:538` 的 `expected_version` 查询参数形同虚设）。
- 影响：HTTP 层完全无法上传/登记材料（含部位照片）；seed、smoke、测试装置只能绕过该端点直接调
  `Database.add_material`。
- 复现：`test_api_pipeline.py::test_post_materials_accepts_expected_version_from_request_body`、
  `::test_post_materials_accepts_expected_version_as_query_parameter`。
- 建议（W1 归属）：端点内把 `expected_version` 放回待校验字典，或让该模型不声明它——
  二选一，必须与 `docs/architecture.md` §4「POST 放请求体」一致。

### D2（blocking）`POST /api/cases/{id}/extract` 恒 409

- 现象：携带正确 `expected_version` 仍返回 `409 {"detail":"案件已被其他操作更新，请刷新后重试"}`。
- 根因：`backend/app/workflow.py:352` `database.claim(case_id, expected_version, actor)` 已递增
  案件版本，随后 `backend/app/workflow.py:442`
  `database.upsert_fields(case_id, fields, replace_existing, expected_version, actor)` 用**同一个**
  `expected_version` 再 `_claim` 一次 → `VersionConflict`。（同段 `upsert_injuries(..., 0, ...)` 是正确的写法。）
- 影响：HTTP 层无法运行字段提取；界面上的「提取字段」按钮必然 409。
- 复现：`test_api_pipeline.py::test_post_extract_returns_stage_result`。
- 建议：第二次写传 `0`（系统内部写），与 `upsert_injuries` 保持一致。

### D3（blocking，红线下沉）`POST /check` 不加载 `ocr_blocks`，N-11/N-1 的阻断项在真实链路不可达

- 现象：先给部位照片写入 `ocr_blocks`（构造 N-11 违规），再 `POST /api/cases/{id}/check`，
  清单里**没有** `PHOTO_CONTENT_READ`；把 `opinion_text` 设成与某识别块完全相同的文本，
  清单里也**没有** `OPINION_FROM_SOURCE`。`OCR_HANDWRITTEN_UNVERIFIED` 同样永不出现。
- 根因：`backend/app/workflow.py:670` `case_data = _case(database, case_id)`（= `Database.get_case`）
  只带 `materials/fields/injuries/documents/checklist/events/corrections`，
  `database.py:860` 的 `get_case` 不返回 `ocr_blocks`，`run_check` 也未调用
  `database.list_ocr_blocks()` 补齐；而 `backend/app/checker.py:287` `_collect_ocr_blocks`
  只认 `case_data["ocr_blocks"]` 与 `material["ocr_blocks"]`。
  全仓检索确认：**没有任何调用方**把识别结果传进 `checker.check_case`（`seed.py`、`smoke.py` 都没有）。
- 影响：`PHOTO_CONTENT_READ`（blocking）与 `OPINION_FROM_SOURCE`（blocking）这两个安全网
  在系统真实运行路径中不可达；`OCR_LOW_CONFIDENCE` 只能退化到「按字段置信度」提示，
  无法逐块报告。
- 复现：`test_api_photos.py::test_inv11_photo_ocr_blocks_trip_the_blocking_check_item`、
  `::test_n1_opinion_copied_from_ocr_text_is_blocked_by_check`。
  校验函数本身正确：`test_checker.py::test_n11_photo_content_read_is_blocking_when_photo_material_has_ocr_blocks`、
  `::test_checker_code_table_matches_documentation[OPINION_FROM_SOURCE]` 均通过（直接喂数据时行为正确）。
- 建议：`run_check` 里补 `case_data["ocr_blocks"] = database.list_ocr_blocks(case_id, page_size=2000)["items"]`
  （或按材料挂在 `material["ocr_blocks"]` 上），与 §5.10 要求的 `storage_root/template_text/...` 同批注入。

### D4（major）`pending_issue` 正文仍写入草稿标记 `【待核对】`

- 现象：把全部 open 阻断项豁免后生成 `pending_issue` 成功（200），但正文仍含 `【待核对】`，
  下一次校核必然产出 blocking 的 `DOCUMENT_DRAFT_MARKER` → 「待签发稿」永远不可能干净。
- 根因：`backend/app/docgen.py:1757`
  `draft_marker = "" if _clean(data.get("doc_status")) == "approved" else C.DRAFT_MARKER`
  只对 `approved` 清空；`run_render` 在 `pending_issue` 时并未把 `doc_status` 语义告知模板，
  模板末段固定 `{{ draft_marker }}`（`docgen.py:1363`）。
- 与文档冲突：§3.2「`internal_draft → pending_issue`：无 open 阻断项、无未解析占位符、
  **无待核对标记**」；R-46「草稿中的待核对标记须在进入待签发稿前处理」。
- 复现：`test_api_pipeline.py::test_r46_pending_issue_body_has_no_draft_marker`。
- 建议：`pending_issue`/`approved` 均清空 `draft_marker`（或在校核中区分“生成的标记”
  与“系统外残留的标记”）。

### D5（major，契约冲突）`engine="paddle"` 不可用时静默回退 sidecar，未返回 409

- 现象：`POST /ocr {"engine":"paddle"}` 返回 200，`payload.engine == "sidecar"`；
  请求的引擎与执行结果不一致，调用方只能自行对比。
- 根因：`backend/app/ocr/__init__.py:124-151` `resolve_engine()` 对不可用引擎回退 sidecar，
  而 `backend/app/workflow.py:277` 正是调用它。
- 与文档冲突：`docs/TODO_DOWNLOADS.md` TODO-DL-2 行写明「调用时抛 `OcrEngineUnavailable`，
  API 返回 409 + 提示改用 sidecar 引擎」，其前言也要求「不静默失败、不伪装成成功」；
  `docs/architecture.md:459` 的 `resolve_engine(preferred="sidecar") # 不可用则回退 sidecar`
  是另一套口径——**两份文档互相矛盾，需要主 Agent 裁决**。
- 复现：`test_api_pipeline.py::test_paddle_unavailable_returns_409_with_todo_dl_2`。
- 建议：显式请求的引擎不可用时返回 409（`detail` 含 `TODO-DL-2`），仅默认/自动选择时才回退。

### 观察（非缺陷，需产品确认）

`Database.create_case` 会把建案时填写的 `commission_unit/…/template_version` 同步成
`case_fields` 行，且 `status="recorded"` 而 `source_material_id` 为空
（`backend/app/database.py:842-850`）。这在每个新案件上都会立即产出 blocking 的
`FIELD_SOURCE_MISSING`（见 `test_checker.py::test_checker_code_table_matches_documentation[FIELD_SOURCE_MISSING]`
与探针输出“校核 25 项，其中阻断 9 项”）。语义上可谓「人工录入的事实同样需要来源」，
但会让「新建即阻断」成为常态，需产品侧确认口径。

## 5. 未解决项 + TODO-DL 记账

- **TODO-DL-1（docxtpl 未装）**：`test_api_pipeline.py::test_docx_engine_status_reports_builtin_fallback_and_todo_dl_1`
  断言 `docx_engine_status()["todo"] == "TODO-DL-1"`（本机 `preferred == "builtin"`）。
- **TODO-DL-2（paddleocr 未装）**：见缺陷 D5；用例保留。
- **TODO-DL-3（npm 依赖不可离线安装）**：Vue 源码与浏览器主流程未构建验证；
  仅以 `frontend/static/` 的 HTTP 与源码级用例覆盖 R-1/R-58/N-5。
- **TODO-DL-4（pypdfium2 未装）**：`imaging.probe` 走 PyMuPDF 分支，已在
  `test_r18_imaging_probe_distinguishes_text_pdf_from_scanned_pdf` 中覆盖（`pytest.importorskip("fitz")`）。
- **TODO-DL-6 / TODO-DL-7（真实样本、真实照片）**：演示数据用例只验证结构与红线，
  不承诺识别率；`test_guards.py::test_r52_acceptance_notes_are_documented_as_todo_downloads` 记账。
- **待主 Agent 裁决**：D1～D5 的实现修复（均在 W1/W2/W3 文件内，W5 无权改）；
  `resolve_engine` 与 TODO-DL-2 的口径冲突（D5）；§4 材料端点 `expected_version` 的最终归属（D1）。
