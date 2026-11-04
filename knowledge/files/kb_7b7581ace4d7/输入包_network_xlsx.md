---
title: 输入包\network.xlsx
tags: [拖入]
---

## 工作表 meta

列：key、value
第 1 行 key: schema_version；value: hc-input-v1
第 2 行 key: snapshot_id；value: SNAP-002
第 3 行 key: topology_version；value: TOPO-002
第 4 行 key: asset_version；value: ASSET-002
第 5 行 key: valid_from；value: 2026-06-01T00:00:00+08:00
第 6 行 key: valid_to；value: 2026-12-31T00:00:00+08:00
第 7 行 key: base_frequency_hz；value: 50
第 8 行 key: timezone；value: +08:00
第 9 行 key: source_system；value: GOLDEN-TEST
第 10 行 key: dictionary_version；value: DICT-002

## 工作表 buses

列：node_id、name、feeder_id、dt_id、vn_kv、phase、connection、neutral_model
第 1 行 node_id: BUS-MV-001；name: 10kV母线；feeder_id: FD-002；vn_kv: 10；phase: ABC；connection: wye
第 2 行 node_id: BUS-MV-002；name: 1号杆；feeder_id: FD-002；vn_kv: 10；phase: ABC；connection: wye
第 3 行 node_id: BUS-MV-003；name: 2号杆；feeder_id: FD-002；vn_kv: 10；phase: ABC；connection: wye
第 4 行 node_id: BUS-LV-001；name: 台区低压母线；feeder_id: FD-002；dt_id: DT-001；vn_kv: 0.4；phase: ABC；connection: wye；neutral_model: TN-C-S
第 5 行 node_id: BUS-LV-002；name: 低压末端；feeder_id: FD-002；dt_id: DT-001；vn_kv: 0.4；phase: ABC；connection: wye；neutral_model: TN-C-S

## 工作表 lines

列：line_id、from_node、to_node、phase、length_km、r1_ohm_per_km、x1_ohm_per_km、r0_ohm_per_km、x0_ohm_per_km、c1_nf_per_km、c0_nf_per_km、max_i_a、in_service、phase_matrix_ref
第 1 行 line_id: LN-MV-001；from_node: BUS-MV-001；to_node: BUS-MV-002；phase: ABC；length_km: 2；r1_ohm_per_km: 0.17；x1_ohm_per_km: 0.34；r0_ohm_per_km: 0.51；x0_ohm_per_km: 1.02；c1_nf_per_km: 10；c0_nf_per_km: 5；max_i_a: 400；in_service: True
第 2 行 line_id: LN-MV-002；from_node: BUS-MV-002；to_node: BUS-MV-003；phase: ABC；length_km: 3；r1_ohm_per_km: 0.17；x1_ohm_per_km: 0.34；r0_ohm_per_km: 0.51；x0_ohm_per_km: 1.02；c1_nf_per_km: 10；c0_nf_per_km: 5；max_i_a: 400；in_service: True
第 3 行 line_id: LN-LV-001；from_node: BUS-LV-001；to_node: BUS-LV-002；phase: ABC；length_km: 0.15；r1_ohm_per_km: 0.32；x1_ohm_per_km: 0.08；r0_ohm_per_km: 0.96；x0_ohm_per_km: 0.24；max_i_a: 600；in_service: True

## 工作表 transformers

列：transformer_id、hv_node、lv_node、sn_kva、vn_hv_kv、vn_lv_kv、vk_percent、vkr_percent、pfe_kw、i0_percent、vector_group、phase、tap_side、tap_pos、tap_neutral、tap_step_percent、zero_sequence_ref、in_service
第 1 行 transformer_id: DT-001；hv_node: BUS-MV-003；lv_node: BUS-LV-001；sn_kva: 1000；vn_hv_kv: 10；vn_lv_kv: 0.4；vk_percent: 4；vkr_percent: 1.2；pfe_kw: 0.6；i0_percent: 0.5；vector_group: Dyn11；phase: ABC；tap_side: lv；tap_step_percent: 2.5；zero_sequence_ref: ZS-DT001；in_service: True

## 工作表 switches

列：switch_id、element_id、terminal、closed、rated_i_a、valid_time、phase
第 1 行 switch_id: SW-001；element_id: LN-MV-002；terminal: 1；closed: True；rated_i_a: 400；valid_time: 2026-06-01T00:00:00+08:00；phase: ABC

## 工作表 loads

列：load_id、node_id、phase、connection、profile_id、model_type、in_service
第 1 行 load_id: L001；node_id: BUS-LV-002；phase: ABC；connection: wye；profile_id: LP001；model_type: constant_pq；in_service: True
第 2 行 load_id: L002；node_id: BUS-MV-003；phase: ABC；connection: wye；profile_id: LP002；model_type: constant_pq；in_service: True

## 工作表 pv

列：pv_id、node_id、phase、connection、rated_p_kw、rated_s_kva、profile_id、control_mode、control_ref、fault_model_ref、in_service
第 1 行 pv_id: PV001；node_id: BUS-LV-002；phase: ABC；connection: wye；rated_p_kw: 100；rated_s_kva: 110；profile_id: PP001；control_mode: fixed_pf；fault_model_ref: FM-PV001；in_service: True
第 2 行 pv_id: PV002；node_id: BUS-MV-003；phase: ABC；connection: wye；rated_p_kw: 300；rated_s_kva: 330；profile_id: PP002；control_mode: fixed_pf；fault_model_ref: FM-PV002；in_service: True

## 工作表 sources

列：source_id、node_id、vm_pu、angle_deg、r1_ohm、x1_ohm、r0_ohm、x0_ohm、source_case_id、z2_assumption_ref
第 1 行 source_id: SRC-001；node_id: BUS-MV-001；vm_pu: 1.02；r1_ohm: 0.5；x1_ohm: 2；r0_ohm: 1.5；x0_ohm: 6；source_case_id: MAX-MODE；z2_assumption_ref: Z2-EQ-R1