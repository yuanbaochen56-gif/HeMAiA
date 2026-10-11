"""Seal hash-only manifest and independent result, then append the authorized handoff."""
import datetime,json,pathlib,shutil,subprocess
from zoneinfo import ZoneInfo
from s1b_inventory_r6 import sha,git,seal_check
W=pathlib.Path('/volume1/users/r1015708/hemaia_bingo_rlink/s1b_20261011');R=W/'evidence/S1bPreflightR6';HOME=pathlib.Path('/users/students/r1015708/thesis')
def write(n,d):(R/n).write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
assert not (R/'manifest.json').exists() and not (R/'result.json').exists()
now=datetime.datetime.now(ZoneInfo('Europe/Brussels'));assert shutil.disk_usage(W).free>=10*2**30
models=json.loads((R/'model_results.json').read_text());assert models['total']==models['passed']==139
for name in ('validation_results.json','reference_stream_check.json','inventory_after.json'):
 assert (R/name).exists()
immut=json.loads((R/'inventory_after.json').read_text());assert not any(immut['comparison'].values())
# Make the scheduler-EOC vs final application error boundary explicit in all proposals.
g=json.loads((R/'proposed_graph.json').read_text());g['termination_paths']['disabled_wrong']='resolver publishes corrupted P with protection off; consumer runs; scheduler reaches host EXIT/EOC, then final golden post_execute returns2, main/startup completion5, check_finish fatal. This intended wrong baseline is separate from resolver FAIL with no consumer.';write('proposed_graph.json',g)
a=json.loads((R/'architecture_proposal.json').read_text());a['decision']['disabled_primary_fault']='Protection off deliberately publishes P; schedule reaches EOC and final golden fails, producing application return2/word5/FATAL. Not an on-protection recovery failure or C3 frozen-table change.';a['finite_argument']['source']+=' A disabled-primary wrong baseline reaches scheduler EXIT normally but post_execute golden returns2, so application completion is5/fatal.';write('architecture_proposal.json',a)
p=json.loads((R/'gate_proposal.json').read_text());p['disabled_wrong_boundary']='G-consume/once remain satisfied for disabled P publication; correct-bytes gate is intentionally false in disabled primary injection only, as predeclared in new scenario table. Scheduler EOC and final application return2/fatal are distinct; no model success-completion1 for a wrong golden result.';write('gate_proposal.json',p)
write('run_provenance.json',dict(start_label='S1bPreflightR1',final_label='S1bPreflightR6',own_tool_failure_history=[dict(label='S1bPreflightR1',reason='manifest.json checksum confused with checksum-file hash'),dict(label='S1bPreflightR2',reason='fixture constructor supplied total slot count instead of device-core count'),dict(label='S1bPreflightR3',reason='bytes in third-override evidence not JSON-serializable'),dict(label='S1bPreflightR4',reason='disabled raw status sentinel would be falsely counted by inherited detector post_execute'),dict(label='S1bPreflightR5',reason='missing golden post_execute error-return completion semantics in own model')],old_records_preserved=True,product_failures=False,infra_same_scene_retries=0,background_processes='none',system_simulations=0,product_builds=0,git_commits=0,pushes=0,prs=0,main_tree_mutations_by_this_run=0,agents_spawned=0,Claude_sessions_or_processes_accessed=False,tracked_changes=immut['comparison'],commands=['python -B tools/s1b_fixture_r6.py','python -B tools/s1b_tests_r6.py','python -S -B tools/s1b_stdlib_r6.py','python -B tools/s1b_reproduce_r6.py','python -B tools/s1b_proposals_r6.py','python -B tools/s1b_validate_r6.py','python -B tools/s1b_inventory_r6.py start/finish'],reproduction_note='Absolute task paths embedded by design; preserve sealed labels and use a new label/root for future reruns. Stdlib replay consumes the frozen expression fixture and never imports real compiler packages. Do not rerun old tools into sealed evidence.'))
# Bind source inputs plus every owned new tool and evidence file, including prior failures.
manifest={}
for root in (W/'tools',W/'evidence'):
 for f in sorted(root.rglob('*')):
  if f.is_file() and f.name not in ('manifest.json','manifest.sha256','result.json','result.sha256'):
   manifest[str(f)]=sha(f)
audit=json.loads((R/'source_audit.json').read_text())
for source in audit['claims']:manifest[source['path']]=source['sha256']
manifest.update(audit['compiler_modules'])
# Required W13 reference seal/checksum/results/prediction and generated inputs.
refs=json.loads((R/'reference_inputs.json').read_text())
for item in refs['bindings'].values():manifest[item['path']]=item['sha256']
ref=W.parent/'s1_20261010/evidence/FinalS1R3'
for f in (ref/'manifest.json',ref/'manifest.sha256'):manifest[str(f)]=sha(f)
for f in (W.parent/'s1_20261010/evidence/ImagesS1R8/dma_chain_sdc_2cluster_sdc/generated').iterdir():
 if f.is_file():manifest[str(f)]=sha(f)
for f,want in manifest.items():assert sha(f)==want,f
write('manifest.json',dict(sorted(manifest.items())));digest=sha(R/'manifest.json');(R/'manifest.sha256').write_text(digest+'\n')
result=dict(status='STOP_REVIEW',task='S1b-0',sealed=now.isoformat(),scope='OFFLINE_MODEL_AND_ARCHITECTURE_ABI_GATE_PROPOSALS_ONLY',manifest_sha256=digest,manifest_entries=len(manifest),offline_checks=dict(semantic_contract_tests=139,semantic_contract_passed=139,stdlib_replay_cases=102,stdlib_counterexample_checks=8,w13_dispatch_done_reproduction_cases=6,w13_prediction_comparisons=6,w13_R3_R4_observation_comparisons=12,additional_compiler_type_conflict='REJECTED',source_claims=31,reference_seal_entries=11801,reference_seal_differences=0),expression_only=dict(real_read_only_compiler=True,descriptor_count=28,normal=16,dummy_set=6,dummy_check=6,private_cerf_group=0,proposed_C_symbols_absent=True,product_compilation=False),product_pass=False,simulations_run=0,product_files_modified=[],tracked_source_changes=immut['comparison'],product_head=git(W,'rev-parse','HEAD').strip(),reference_head=git(W.parent/'s1_20261010/HeMAiA','rev-parse','HEAD').strip(),actual_main_head=git(HOME/'HeMAiA','rev-parse','HEAD').strip(),bingo_head=git(HOME/'bingo_hw_manager','rev-parse','HEAD').strip(),feasibility='SOURCE_CONDITIONAL_CAUSAL_FEASIBILITY; PHYSICAL_TIMING_AND_FAILURE_PROTOCOL_PENDING_REVIEW_AND_SYSTEM_VALIDATION',required_review=['Exact RECOVER flag/SDC_QUIET/TEST_CFG and existing sdc_enable switch contract','Host initialization node, rawstatus enabled sentinel/disabled0, state96B and args48B/80B/report ABI/file list','Host FAIL2 no-DONE and completion5/fatal conditional proof including existing IRQ clear poll; decide required future negative system probe','Explicit host fences with actual generated CVA6 cache configuration/readback proof','G-release1us eligibility/skip/dummy read-only observer and pre-run cost envelopes; no physical timing PASS yet','Public graph successor lookup and explicit status0..1/base mapping, no new compiler method'],next='STOP_REVIEW; do not start S1b-1/R1/B2 until separate Codex review authorizes it',old_facts_unchanged=True,failure_records_preserved='S1bPreflightR1 through R5, all own offline-tool issues',free_GiB=round(shutil.disk_usage(W).free/2**30,2),background_processes='none')
write('result.json',result);result_digest=sha(R/'result.json');(R/'result.sha256').write_text(result_digest+'\n')
# Hash-only seal excludes independent result and append-only handoff to avoid circularity.
note=HOME/'NOTE_from_cursor_agent.md';prefix_length=note.stat().st_size;prefix_hash=sha(note)
assert prefix_hash==json.loads((R/'inventory_before.json').read_text())['docs']['NOTE_from_cursor_agent.md']
artifacts=['architecture_proposal.json','abi_proposal.json','gate_proposal.json','model_results.json','tests.log','source_audit.json','proposed_graph.json','stdlib_results.json','w13_reproduction.json','validation_results.json','reference_stream_check.json','inventory_after.json']
artifact_lines='\n'.join(f'- `{name}` SHA `{sha(R/name)}`' for name in artifacts)
text=f'''

## Codex S1b-0交接（STOP_REVIEW）（{now.strftime('%Y-%m-%d %H:%M:%S CEST')}）

本轮完成 S1b-0 的离线设施、字节/调度模型、真实 compiler 表达验证及架构/ABI/门闸提案，**只到 STOP_REVIEW，不开始 S1b-1/R1/B2**。这是模型/提案检查通过，不是产品 PASS。未改产品源码、未构建产品镜像、未启动 Questa/runner/系统仿真、未提交/push/PR/合并，未启动或触碰任何 Claude/Cursor 会话、进程，也没有派生 agent。当前用户任务的停审边界优先于 bbot 08:56 的宽泛自查续跑规则；DESIGN 未改。

W14 已由 bbot 创建，本轮沿用 `/volume1/users/r1015708/hemaia_bingo_rlink/s1b_20261011`，分支 `s1b-20261011`、HEAD `8b944254a1b2856a92448b94ac21770973177a75`；不重建/重置。最终证据 **`{R}`**，设施在 W14/tools，均未提交。hash-only manifest `{digest}`（{len(manifest)}项），独立 result `{result_digest}`；两者分别在 manifest.sha256/result.sha256，不作互相自引用。

{artifact_lines}

### 验证与场景

- 最终 `tests.log` **139/139** 离线检查通过，独立 `python -S -B` 重放 **102** 字节/调度场景、**8** 反例及图合同通过，证明模型及封存 fixture 可在仅标准库环境读取执行；真实 compiler 表达/冲突探针另用原有 networkx/matplotlib，禁止自动安装路径，所有配置缓存放本轮 evidence。
- 健康关/开均 third=0、commit/report各1、canonical正确。阶段0/1 × P/S × offset0/17/63 × mask1/4/128 共 **36** 单任务输出坏字节：只有 mismatch 事务 third=1，整缓冲区表决后 canonical正确，P坏选S、S坏选P；这表示当前可靠 host/输入/compare/status 假设下的模型结果，不是系统覆盖率。额外“阶段0修复后阶段1 primary再坏”验证真正数据依赖链，单列为两事务扩展、不计一次单故障覆盖。
- 关闭保护的 P0/P1 注入预期 canonical错误，S0注入因影子未派发而不生效。关场 S/compare 没有 DISPATCH/DONE，模型用 skipped checkout 的 dep_set/tag释放让 resolver 合法进入，不把 skip 当 DONE。原始 rawstatus关场保持0；软件 detect_code sentinel及detect_valid0避免把未检测当健康检测。scheduler EOC与应用最终结束区分：关闭 primary 的最终 golden FAIL后 return2/完成字5/fatal，而不是成功完成字1。
- 无两份整缓冲区相同（两个阶段）、status越界/未写/负值/一致码与不一致数据矛盾/非零码却数据相同/首错偏移矛盾、第三次不可靠反例均安全拒绝，不commit、不写resolver DONE、无最终consumer DISPATCH；FAIL路径跳过post_execute并预测完成字5/fatal。它们是超范围安全拒绝，不能当真实系统覆盖。
- 缺compare、缺stage1 canonical writer、缺最终commit依赖，提前放行consumer的动态反例，输入过早覆盖、P/S alias、重复commit/报告、负/重复/稀疏/错位/未对齐索引，private CERF writer冲突/32组耗尽、缺动态writer依赖、共享core跨cluster HOL、全局入口FIFO8 HOL、live tag复用均被合同或模型拒绝。显式稀疏映射的备选单测通过只是示范可审映射，**本提案未采用稀疏布局**。合法图64种有限duration调度均到EOC；安全tag在drain后可复用。
- 新调度模型对 W13 六场 **6/6** 逐核DISPATCH顺序、DONE集合复现，同时与封存预测和R3/R4提取观测（12场）一致；预期仅在模型运行之后作为比较输入，没有抄预期生成结果。没有改/导入执行W12旧模型。
- 最终31条源码 claim（路径、SHA、行号、原文截取）和全部真实compiler导入模块hash一致。FinalS1R3 hash映射逐行流式复核 **11801/11801、差异0**，manifest.json SHA仍 `2e7a69579fe410ab325118c8bcd3d157246d272ea7c3af6202052209d343e57c`；manifest.sha256本身SHA `4ef16a080b86d32f43f068333ce04e42386741a26b0d4872135e4abe672c3bbb`。

### 架构/接口提案及证明边界

真实未改 compiler 生成28个描述符：16 normal（含entry/exit）+6 dummy_set+6 dummy_check，CERF私有group0，4bit tag；节点、核/slot、逻辑/变换边、发射序、buffer读写/生命周期、tag set→clear明细均在 proposed_graph。旧默认排序未改；新阶段1的祖先闭包自然包含阶段0 compare/resolver，故不能照搬S1a“所有primary先于所有observer”的观察者假设。

两个64B设备DMA阶段：P在0:1，S/compare在1:1，输入、P/S/T、canonical、sink均chip0 L3，永不alias/回收；第二阶段只从C0读，最终host sysDMA consumer只从C1读。新增待审 host init（0:2）在P/S之前初始化2×96B软件状态、enabled snapshot和rawstatus（开=UINT32_MAX、关=0）。compare DMA wait/fence→compare DONE→resolver acquire fence/read→必要时自身host kernel从稳定输入独立64B标量重算T一次→整缓冲区 P=T/S=T→完整发布canonical/屏障→resolver SUCC/DONE→消费者。无golden resolver参数，不把mismatch诊断为primary坏，不自动fence/park，不制造CERF_FB，不排队第三个条件任务。原始检测code不擦掉。

候选flag **BINGO_SDC_RECOVER** 要求 SDC+QUIET+TEST_CFG=1，用原有sdc_enable；不增加T1字、CSR、描述符位、ready/done或EVLOG码。init参数48B，resolver参数80B，字段都是uint64_t且scratchpad最后；软件状态96B/事务，12个uint32_t和6个host-mcycle uint64_t。原始检测code、有效观察位、third/selected/commit/report/outcome/failure/phase分开，完整offset/初始化/所有权/枚举/打印格式在abi_proposal。新公共符号/类型/两个Python参数类均只提案，尚不存在产品实现；不需新增 compiler 方法，用公共 successor查询绑定唯一compare及显式status地址。indices严格0..1、偏移0/4，实际base/地址必须在后续镜像封存，不能借min猜base。

拟改文件详列 abi_proposal：新 host_sdc_recover.h；host_kernel_lib.h新flag隔离include；公共host_kernel_args.h和bingo_kernel_args.py新增两参数类型/状态；新 dma_chain_sdc_recover_2cluster 的main_bingo.py/Makefile/params.hjson/datagen；driver旗标白名单/校验及新checker/scenario；新增compiler/checker/C语义测试。bingo_dfg.py、bingo_sdc.py、原kernel/workload、RTL/CSR/runtime ready/done无需修改。旧73workload/74mode生成及W13共16镜像逐字节、旧关闭逐ps合同必须在S1b-2/后续授权回归机械证明，**本项未把它们称为已验证产品兼容性**。

有限失败源码链：bingo_api.c:1085–1106 FAIL2不写DONE→生成bingo_dfg.py:2681早返回→main.c:62–72清IRQ并return→start.S:21–37写完成字5→check_finish.sv.tpl错误fatal；成功word1 finish。但 `heterogeneous_runtime.h:232–235/162–167` 清IRQ后有poll，本证明依赖可靠CSR响应及无晚到设备退出IRQ。当前图所有设备exit均在最终consumer之后，resolver失败不释放它们，所以无该退出IRQ来源；模型的SIM_FATAL等协议事件是这一条件证明的抽象，**没有真实失败系统/WCET证据**。设备队列失败后可仍等依赖，没有强制清空或自动fence。审核须决定是否需新增负例系统探针；若条件证明不足，先报告此缺口再停，不能改runtime或加timeout绕过。

内存可见性：sdc.h:32–46等待status DMA和seq_cst fence；L3实际可缓存。CVA6 template启用flush-on-fence，controller.sv:175–225等ack，WT missunit等待writebuffer空/MSHR完成并失效cache。提案要求host读前及canonical/metadata发布后显式fence/编译器memory clobber；不能仅靠volatile。具体生成RTL/cache配置、真实读回和IRQ/完成字传播仍待S1b-2/3验证。

G-consume/G-vote/G-once/G-status/G-release与有限失败/正常EOC门闸逐条写入gate_proposal。G-release维持 **1µs=1000000ps**，含迟到入口/HOL等待；需只读波形/设施观察skip-retire和dummy/tag就绪，不能以日志缺失充当skip DONE。模型因果tick不当ps。新恢复workload等待检测/commit是机制成本，旧S1a G-crit/1µs及冻结数字不变。预跑预算为待审提案：DMA/QUIET compare各64µs，entry/init/consumer/device-exit/host-exit各512µs，resolver健康含报告2048µs、mismatch含报告3072µs，旧健康串行8592µs为未实测R6估计。2026-10-11 18:30 CEST用户改健康门闸为8700.000µs：padded参考实测8613.922µs（task10 entry DISPATCH→sim end），精确+1%为8700.06122µs，向下取整实际略低于1%；按明确数字8700执行，不宣称>=1%。一mismatch9616µs/两mismatch10640µs及FAIL后完成字2048µs不变；新host值未实测、不是硬WCET或批准阈值，必须在系统前审核冻结，不能失败后放宽。G-release实际延迟没有PASS证据。

### 原树未动、失败历史、授权1–10

主树实际HEAD在开工记录时已经是 **20cbbd41**（父4aaed8bb和8b944254，bbot09:00按用户决定合入），不再是启动文本里的4aaed8bb；本轮没有合主树。bingo unified-ctrl仍6a8fd7b，W13/HeMAiA仍8b944254。本轮前后两主树/W13/W14全部tracked hash差异0，主树.gitignore、bingo README.md、W13 Bender.local及11个untracked生成文件的status/hash不变；11801旧封存再核0差异。W14没有与旧工作树共享可写源inode，工具/证据nlink全部1；python始终-B/禁止字节码、缓存在本轮独立目录。DESIGN hash不变；NOTE本段只追加。

自己工具问题逐次保留并使用新label，不能当旧证据/产品门闸失败改判：R1混淆manifest/checksum文件SHA；R2误把总slot数传给期待device-core数的构造器，真实compiler拒绝；R3 bytes证据JSON编码错误；R4遗漏关闭rawstatus0与原post报告的合同；R5遗漏golden post_execute return2的应用完成协议。旧stop.json/log/trace/提案/失败均原样保留，R6补足后139检查及后续复核全通过，没有增加容差、timeout或改旧checker/预测。

1. 设施/测试：新标准库模型、fixture/checker、独立oracle、反例、source/hash审计；自己的问题保留原证据后改新label。
2. RTL内部授权未用，没有RTL实现。
3. 仅选新离线fixture2×64B和待审预算；未改批准门槛/参数，磁盘开工≥10.5GiB、运行≥10GiB，全程约84GiB。
4. 新软件报告字段/格式仅ABI提案，没有产品打印或EVLOG改动。
5. 只执行离线compiler生成/检查；没有产品build、仿真或提交拆分。
6. 无license/runner重试；read-only broad find曾在W12保护overlay work目录得到Permission denied，已记source_audit，无写拒绝、无绕过、不再访问这些目录。
7. 记录bbot外部合并及规则文件变化，按本次用户范围保持STOP_REVIEW；旧脏文件/无关差异不动。
8. 按先源码/模型/表达再提案/封存顺序完成；所有门闸未用于授权任何后续阶段。
9. 没有产品重构；本地离线BingoKernelArgs扩展实例不猴补产品方法/源码，也未改变默认排序。
10. 不复用旧产品PASS替代本轮证明；W13封存只作hash绑定和结构对照，所有本轮离线检查另跑；默认/旧flag镜像与逐ps证明留给下一授权阶段。

局限：仅copy型事务和可靠host/输入/compare/status的瞬态输出模型；不覆盖共同输入损坏、相关P=S同错、永久坏核、GEMM、多芯片、fail-stop+SDC或比较器故障；混合host重算不叫三份独立设备TMR。64个duration调度不是RTL全部交错/形式WCET证明；精确cache/bus/UART/IRQ延迟没有仿真证据。alias/input覆盖/重复commit或报告是合同拒绝，不假定系统可恢复这些超范围问题。关闭注入wrong只列新表，不回填C1–C6。QUIET仅host检测观察，不编造设备第二报告。

必须Codex裁定：上述flag/开关、host init、96B状态与48/80B参数和公共符号/文件清单；稳定输入/整缓冲区vote/publish边界；条件有限FAIL论证是否足够及后续负例系统验证；生成cache/地址绑定；公共successor绑定是否接受；G-release只读就绪观察方案及预跑时序预算。**审核之前不写产品、不开始S1b-1。**

自家后台PID/console：**无**；所有本轮命令已完成，未检查其他agent进程。接手先流式核manifest及独立result，阅读三份proposal、source_audit/proposed_graph/model_results和旧failure记录；只做单独Codex裁定，旧主树/W13/W12/bingo/冻结证据禁止碰。本轮到此结束，**STOP_REVIEW**。
'''
with note.open('ab') as f:f.write(text.encode())
# Verify append-only prefix and final seals without writing any more evidence.
import hashlib
with note.open('rb') as f:assert hashlib.sha256(f.read(prefix_length)).hexdigest()==prefix_hash
for name,want in json.loads((R/'manifest.json').read_text()).items():assert sha(name)==want,name
assert sha(R/'manifest.json')==(R/'manifest.sha256').read_text().strip()
assert sha(R/'result.json')==(R/'result.sha256').read_text().strip()
print(json.dumps(dict(status='STOP_REVIEW',manifest_sha256=digest,result_sha256=result_digest,manifest_entries=len(manifest),note_append_bytes=len(text.encode()),evidence=str(R),free_GiB=result['free_GiB']),ensure_ascii=False))
