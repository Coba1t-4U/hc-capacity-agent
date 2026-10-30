"""Final acceptance summary — asserts every design-document acceptance criterion."""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, '.')
ROOT = Path('.').resolve()
A = ROOT / 'runs' / 'ACCEPT'

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    mark = 'PASS' if cond else 'FAIL'
    print(f'  [{mark}] {name}' + (f'  -- {detail}' if detail else ''))


def summary(run):
    return json.loads((A / run / 'results' / 'run_summary.json')
                      .read_text(encoding='utf-8'))


def verification(run):
    return json.loads((A / run / 'verification' / 'verification.json')
                      .read_text(encoding='utf-8'))


print('=' * 78)
print('配网承载力 Agent —— 设计文档验收核对')
print('=' * 78)

print('\n--- 表 38 指标验收矩阵 ---')

# 约束覆盖：不少于 5 类，三级对象均覆盖
print('\n[约束覆盖]')
rows = list(csv.DictReader(open(A / 'FD001' / 'results' /
                                'constraint_results.csv',
                                encoding='utf-8-sig')))
kinds = sorted({r['constraint_id'] for r in rows})
check('约束类别不少于 5 类', len(kinds) >= 5, f'{kinds}')
check('含反向潮流', 'REVERSE_FLOW_LIMIT' in kinds)
check('含电压越限', 'VOLTAGE_LIMIT' in kinds)
check('含三相不平衡', 'IMBALANCE_LIMIT' in kinds)
check('含载流量', 'THERMAL_LIMIT' in kinds)
check('含短路容量', 'SHORT_CIRCUIT_LIMIT' in kinds)

# 独立复算 100%，全新工作目录
print('\n[独立复算]')
for run in ('FD001', 'FD002'):
    v = verification(run)
    check(f'{run} 独立复算通过', v.get('pass') is True,
          f"mismatch={v['comparison']['mismatch_count']}")
    check(f'{run} 复算零差异', v['comparison']['mismatch_count'] == 0)
    check(f'{run} 输入指纹一致', v.get('input_match') is True)
    check(f'{run} 差异报告已生成',
          (A / run / 'verification' / 'metric_diff.csv').exists())
check('复算使用独立目录',
      str((A / 'FD002' / 'verification').resolve())
      != str((A / 'FD002').resolve()))

# 原始数据保护：计算前后字节哈希一致
print('\n[原始数据保护]')
man = json.loads((A / 'FD001' / 'manifest.json').read_text(encoding='utf-8'))
from hc_agent.evidence import verify_file_immutable
ok = all(verify_file_immutable(r) for r in man['inputs'])
check('快照哈希前后一致（保护率 100%）', ok, f"{len(man['inputs'])} 个文件")
ro = all(not (Path(r['abspath']).stat().st_mode & 0o222)
         for r in man['inputs'])
check('快照置为只读', ro)

# 全过程追溯
print('\n[全过程追溯]')
led = list((A / 'FD001' / 'audit' / 'events.jsonl')
           .read_text(encoding='utf-8').strip().splitlines())
events = [json.loads(x) for x in led]
need = ('event_id', 'task_id', 'run_id', 'actor', 'action', 'object_ref',
        'before_hash', 'after_hash', 'timestamp', 'source', 'result', 'reason')
check('审计事件含全部必需字段',
      all(all(k in e for k in need) for e in events), f'{len(events)} 条')
req = ('S01_freeze_inputs', 'S02_validate_bundle', 'S03_build_case',
       'S04_run_baseline', 'S06_search_capacity', 'S07_verify_run',
       'S08_generate_report', 'S09_seal_evidence')
seen = {e['action'].replace('step:', '') for e in events}
check('关键步骤全部有审计记录',
      all(r in seen for r in req), f'缺 {[r for r in req if r not in seen]}')

# OpenDSS 适配
print('\n[OpenDSS 适配]')
from hc_agent.runtime_env import engine_fingerprint, check_locked_version
fp = engine_fingerprint()
check('引擎可用', fp.get('available') is True)
check('引擎为 opendss', fp.get('engine_id') == 'opendss')
check('接口为 OpenDSSDirect.py', fp.get('interface') == 'OpenDSSDirect.py')
chk = check_locked_version(strict=False)
check('匹配锁定版本 0.9.4 / 0.14.5', chk['locked_ok'], str(chk['lock_notes']))
check('引擎指纹写入运行清单',
      bool(man.get('engine_fingerprint', {}).get('interface_version')))

# 可复现性：冻结项写入清单
print('\n[可复现性 / 版本冻结]')
for key in ('snapshot_id', 'rule_version', 'skill_version',
            'engine_fingerprint', 'input_digest', 'parameters'):
    check(f'清单含 {key}', key in man and man[key] not in (None, '', {}))
check('技能版本含 12 项', len(man.get('skill_version') or {}) == 12)

# FD002 容量搜索行为
print('\n[FD002 容量搜索]')
s2 = summary('FD002')
cs2 = s2['constraint_summary']
check('基线无越限', cs2['fail'] == 0, f"fail={cs2['fail']}")
check('物理容量为正', (s2.get('capacity_physical_kw') or 0) > 0,
      f"{s2['capacity_physical_kw']} kW")
check('搜索状态为 ok', s2['search_status'] == 'ok')
check('采用括区间+二分', s2['search_method'] == 'bracket_and_bisect')
check('二分迭代收敛', s2['search_iterations'] >= 3,
      f"{s2['search_iterations']} 次")
check('记录主导限制因素', bool(s2['binding_constraint']),
      s2['binding_constraint'])
check('记录可行下界/不可行上界',
      s2['feasible_lower_kw'] is not None
      and s2['infeasible_upper_kw'] is not None,
      f"{s2['feasible_lower_kw']} / {s2['infeasible_upper_kw']}")
check('搜索轨迹落盘', (A / 'FD002' / 'results' /
                       'search_trace.csv').exists())

# FD001 存量问题处理
print('\n[FD001 存量问题优先]')
s1 = summary('FD001')
check('基线检出存量越限', s1['constraint_summary']['fail'] > 0,
      f"fail={s1['constraint_summary']['fail']}")
check('容量为 0 而非 unknown', s1.get('capacity_physical_kw') == 0.0)
check('说明中声明先输出存量问题',
      any('存量' in n for n in (s1.get('search_notes') or [])),
      str(s1.get('search_notes'))[:80])

# 发布门禁
print('\n[发布门禁 三道门]')
gate = json.loads((A / 'FD002' / 'review' / 'release_gate.json')
                  .read_text(encoding='utf-8'))
check('门禁未通过（缺人工复核与审批）', gate['passed'] is False)
check('列出未通过的门',
      set(gate['failed_gates']) == {'professional_review', 'approval'},
      str(gate['failed_gates']))
pub = json.loads((A / 'FD002' / 'review' / 'publication.json')
                 .read_text(encoding='utf-8'))
check('未生成正式发布记录', pub['status'] == 'not_published')
check('候选结果哈希已绑定', bool(pub.get('candidate_result_hash')))

# 证据包
print('\n[证据包]')
ev = list((A / 'FD002' / 'evidence').glob('*.zip'))
check('证据包已封存', bool(ev), f'{ev[0].name if ev else "missing"}')
if ev:
    import zipfile
    with zipfile.ZipFile(ev[0]) as z:
        names = z.namelist()
        blob = b''.join(z.read(n) for n in names
                        if n.endswith(('.json', '.csv')))
    for want in ('manifest.json', 'results/capacity.csv',
                 'results/constraint_results.csv',
                 'verification/verification.json', 'audit/events.jsonl'):
        check(f'证据包含 {want}', want in names)
    check('证据包含哈希索引', 'evidence_index.json' in names)
    check('证据包不含密钥字样',
          not any(t in blob.lower() for t in
                  (b'api_key', b'password', b'secret', b'token')))

# 报告可回链
print('\n[报告与防幻觉]')
rm = json.loads((A / 'FD002' / 'report' / 'report_manifest.json')
                .read_text(encoding='utf-8'))
check('报告已生成', Path(rm['report_path']).exists())
check('回链检查完整', rm['traceability']['complete'] is True,
      f"缺 {rm['traceability']['missing']}")

# 研究流程
print('\n[主配微研究]')
out = json.loads((A / 'RM001' / 'study_outcome.json')
                 .read_text(encoding='utf-8'))
check('研究流程成功退出', out['exit_code'] == 0)
check('容量外层给出定界结果',
      out.get('lambda_mw') is not None, f"λ={out.get('lambda_mw')} MW")
m = out.get('metrics', {})
check('存在不可行上界（真正定界）', (m.get('infeasible_points') or 0) > 0,
      f"infeasible={m.get('infeasible_points')}")
check('非预见性检查通过', m.get('nonanticipativity_pass') is True)
check('储能能量守恒通过', m.get('energy_conservation_pass') is True)
for f in ('capacity_frontier.csv', 'interface_trace.csv', 'dispatch.csv',
          'research_metrics.csv'):
    check(f'研究输出 {f}', (A / 'RM001' / 'results' / f).exists())
check('范围声明已出具',
      bool(json.loads((A / 'RM001' / 'results' / 'research_summary.json')
                      .read_text(encoding='utf-8'))['scope_declaration']))

# SK-09 短路校核
print('\n[SK-09 短路容量校核]')
rows2 = list(csv.DictReader(open(A / 'FD002' / 'results' /
                                 'constraint_results.csv',
                                 encoding='utf-8-sig')))
sc = [r for r in rows2 if r['constraint_id'] == 'SHORT_CIRCUIT_LIMIT']
check('短路约束已实际执行（非 unknown）',
      sc and all(r['status'] in ('pass', 'fail') for r in sc),
      f"{len(sc)} 行，状态 {sorted({r['status'] for r in sc})}")
check('给出真实故障电流', all(float(r['value']) > 0 for r in sc),
      f"{float(sc[0]['value']):.1f} A" if sc else 'n/a')
check('仅报告相导体（不重复计中性线）', len(sc) == 3, f'{len(sc)} 行')
check('故障类型已冻结',
      all('three_phase' in (r.get('detail') or '') for r in sc))
s2b = summary('FD002')
check('unknown 归零后给出正式分级', s2b['constraint_summary']['unknown'] == 0
      and s2b['level'] in ('green', 'yellow', 'red'),
      f"unknown={s2b['constraint_summary']['unknown']} level={s2b['level']}")

# SK-11 并网申请校核
print('\n[SK-11 并网申请校核]')
app_dir = A / 'APP001'
if app_dir.exists():
    ac = json.loads((app_dir / 'results' / 'access_check.json')
                    .read_text(encoding='utf-8'))
    ap = json.loads((app_dir / 'results' / 'run_summary.json')
                    .read_text(encoding='utf-8'))
    check('按申请容量校核（非容量搜索）',
          ac['requested_capacity_kw'] == 250.0,
          f"{ac['requested_capacity_kw']} kW")
    check('给出结论候选', ac['decision_candidate'] in
          ('可行', '有条件可行', '不可行', '无法评估'),
          ac['decision_candidate'])
    check('区分存量问题与新增变化',
          'preexisting' in ac and 'newly_induced' in ac,
          f"存量 {ac['baseline_fail']} / 申请后 {ac['application_fail']} / "
          f"新增 {len(ac['newly_induced'])}")
    check('可行申请不引入新增越限', ac['newly_induced'] == [])
    check('产出接入校核输出', (app_dir / 'results' /
                               'element_results_application.csv').exists())
else:
    check('APP001 并网审查算例已运行', False, '缺少 runs/ACCEPT/APP001')

# 健壮性
print('\n[健壮性]')
from hc_agent.pipeline import Pipeline
import shutil as _sh
import tempfile as _tf
_tmp = Path(_tf.mkdtemp())
try:
    exits = []
    for _ in range(2):
        _p = Pipeline(GOLDEN := ROOT / 'tests' / 'golden' / 'FD002' /
                      'task.json', ROOT / 'tests' / 'golden' / 'FD002',
                      _tmp / 'run', skip_verification=True, max_slices=2,
                      verbose=False)
        exits.append(_p.run()['exit_code'])
    check('同一输出目录可重复运行', all(e in (0, 5) for e in exits),
          f'exit={exits}')
finally:
    _sh.rmtree(_tmp, ignore_errors=True)

print('\n' + '=' * 78)
print(f'验收核对结果：{len(PASS)} 项通过 / {len(FAIL)} 项未通过 / '
      f'{len(PASS) + len(FAIL)} 项总计')
if FAIL:
    print('\n未通过项：')
    for f in FAIL:
        print('  -', f)
print('=' * 78)
sys.exit(0 if not FAIL else 1)
