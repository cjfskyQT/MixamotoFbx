# Capoeira Retarget

把 Mixamo FBX 动画转换到 **ADV / mGear** 角色绑定、任意自定义 FBX 骨架，或 Maya ASCII（`.ma`）
FK 控制器 —— 直接使用你本机已安装的 Maya，**不需要安装独立的 FBX Python SDK**。

## 安装

```bash
git clone https://github.com/cjfskyQT/MixamotoFbx.git
cd MixamotoFbx
```

整个目录就是程序本体，可以整体移动或改名（路径允许空格和中文），启动脚本会自动定位程序，
**不需要手动设置 `PYTHONPATH`**。

```
MixamotoFbx/
├── ui/                主窗口与骨骼匹配面板
├── maya_engine.py     烘焙核心
├── retarget_tool.py   命令行与界面入口
├── leg_ik.py          双腿 IK 求解
├── foot_contact.py    脚部接触判定与轨迹修正
└── README.md          本文档
```

## 启动

1. 双击 `setup.bat`，检查 Maya 和界面依赖。
2. 双击 `retarget_gui.bat` 打开软件。
3. 选择源 FBX 或动画文件夹、目标绑定及输出目录，点击 Generate。

`setup.bat` 是环境检查入口；没有安装 Maya 时，需要先安装 Maya。若 Maya 未自动找到，可设置 `MAYAPY` 为 `mayapy.exe` 完整路径；`PYTHON` 可指定包含 PySide6/PySide2 的界面解释器。

命令行方式（在仓库根目录执行）：

```powershell
python retarget_tool.py --source "capoeira.fbx" --target "target_rig.fbx" --output "out\capoeira.fbx" --mode adv
```

`--mode` 取 `adv` / `custom` / `maya`；`--ui` 打开手动骨骼匹配面板。

## 模式和动画

- A：ADV/HIK FBX，自动匹配骨骼。
- B：自定义 FBX，使用 Match bones 手动匹配。
- C：Maya ADV 场景，选择干净的初始绑定 `.ma`，输出可编辑控制器动画。

C 模式默认输出双腿 IK、手臂 FK。保留“双腿转 IK”和“脚部轨迹修正”勾选状态。膝盖平面参考源 FBX 的髋、膝、踝位置，避免反向；输入姿态和绑定必须正确，生成过程会验证烘焙结果。

脚部轨迹使用整段源动画离线规划。先按目标胯宽、腿长和当前源腿方向计算自然脚位，再独立拟合每段支撑的锚点；锚点不继承前一段输出的偏移。接触判断采用整只脚的低位与低速证据，由可靠静止样本向前后确认支撑边界；脚掌滚动时最低点和静止枢轴可以是不同关节。局部地面来自时间窗口内的低位样本，不能把当前最低的一只脚直接当作地面。离地轨迹同时衔接前后支撑，不再用逐帧累计的锁定/释放状态。

身体水平运动保留源轨迹。为匹配骨长，只允许离线平滑的垂直补偿，上限为较短腿长的 10%；无法满足脚位与腿长约束时报告具体帧号。源与目标的胯宽、上下腿比例不同，会需要局部腿姿调整，不能同时强求所有源关节位置、身体位置和骨长完全一致。

脚部轨迹每 1/8 帧求解和烘焙。生成完成后，每 1/16 帧检查实际脚位与解剖锚点的绝对位置，以及身体与源轨迹的差异；脚位误差超过 0.1 厘米或身体轨迹误差超过 0.05 厘米就停止生成。验收不再把输出的第一帧脚位当作正确锚点。此检查保留源动画本身的移动，不会把源动画已有的滑步强行锁死。更新程序后需从源 FBX 重新生成；旧输出不会自动改变。

已有纯 FK 动画可以点击“已有 FK 动画转双腿 IK…”并另存 `.ma`。这个入口保留原 FK 姿态；FK 本身有误时，需要结合源 FBX 重新生成。

在 Maya 中用 `IKLeg_L/R` 修改脚部，用 `PoleLeg_L/R` 调整膝盖方向，用 `IKToes_L/R` 调整脚趾。原 FK 曲线用于小腿扭转参考，请保留。

源动画、目标绑定和输出目录都在界面选择。软件不附带示例动画、旧输出或诊断数据，不会覆盖输入动画或绑定文件。请使用可信、干净的 MA 绑定；恶意脚本检查不能绕过。

## 文件和移动

启动脚本、环境检查和核心 `.py` 都在本目录，`ui` 是必要的界面代码。请保留这些程序文件。整个目录可以直接移动，路径可以包含空格或中文；启动脚本会自动定位程序。

任务临时文件使用系统临时目录，并在任务结束后清理。可用 `CAPOEIRA_TEMP_DIR` 指定其他临时目录。

`retarget.bat` 是可选的 ADV FBX 批处理入口，需要预先设置 `MIXAMO_DIR` 和 `TARGET_FBX`，可设置 `OUT_DIR`；已有输出会跳过。

如果在已运行的 Maya 内使用 commandPort，移动程序或更新代码后应重启 Maya，让会话加载新路径和代码。默认独立 worker 模式不需要开启 commandPort。

## 许可

MIT —— 见 [LICENSE](LICENSE)。

## 第三方说明

本仓库**不包含**任何 Maya、Mixamo、mGear 或 Advanced Skeleton 的代码与资产。工具通过官方
文档化的 Python / FBX 接口驱动你本机的 Maya 安装，并读取 Mixamo 导出的 FBX。映射表中引用的
控制器*名称*（`FKRoot_M`、`FKScapula_L` 等）属于功能性标识符，不是被再分发的绑定内容。

`scene_safety.py` 中的字符串是恶意代码检测特征码（Autodesk ScriptExploit 家族），
用于拒绝加载被篡改的 `.ma` 文件 —— 纯防御性、仅用于检测的用途。
