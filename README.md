# 🛰️ 飞无止境卫星凌日/行星预测器 (Satellite Transit Predictor)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Skyfield](https://img.shields.io/badge/astronomy-skyfield-orange.svg)](https://rhodesmill.org/skyfield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

一款专为天文爱好者和天文摄影师打造的高精度、多目标卫星凌星预测工具。基于 `skyfield` 天体物理引擎，本软件可用于精准预测国际空间站（ISS）、中国空间站（CSS）、星链（Starlink）等航天器凌日、凌月，甚至凌木星及凌土星的罕见天文现象 [cite: 1]。

---

## 💡 设计思路 (Design Concept)

在天文摄影中，拍摄空间站或卫星凌日/月是一项极具挑战但也极具成就感的任务。传统的预测工具往往面临计算精度不足、国内网络环境下 TLE（双行轨道数据）下载受限、缺乏针对摄影器材预估等痛点。基于此，本软件的设计思路为：
1. **极致精度**：底层采用 NASA JPL `DE421` 星历表与 `skyfield` 库进行真实的三维空间轨道演化计算 [cite: 1]。
2. **高可用与抗封锁**：创新性集成“全网抗封锁智能多源自动切换”机制。支持从 Ivan API、SatNOGS、CelesTrak 等多源拉取数据，同时允许本地缓存与断网环境下的自定义 TLE 文件导入 [cite: 1]。
3. **摄影导向**：内置主镜焦距与相机像元尺寸换算引擎，直接预估凌星事件中目标在画面中占据的像素大小 [cite: 1]。
4. **可视化与交互**：集成 `TkinterMapView` 交互地图与凌星视角模拟小窗，直观展示凌星中心线、可视走廊以及卫星切入视场的相对轨迹 [cite: 1]。

---

## ✨ 主要功能 (Features)

- 🪐 **多目标凌星预测**：支持预测凌日、凌月，以及凌木星、凌土星事件 [cite: 1]。
- 🛰️ **多航天器支持**：内置国际空间站（ISS）、中国空间站（天宫）、各代星链（精准区分初代与大尺寸 V2）、OneWeb 及高亮肉眼可见卫星 [cite: 1]。
- 🗺️ **交互式地图引擎**：支持地名搜索定位、右键快速设为观测点，并在地图上实时绘制高亮可视走廊（中心线、左/右边缘） [cite: 1]。
- 📡 **抗封锁 TLE 方案**：支持多数据源智能回退、强制缓存读取以及用户自定义 TLE 纯文本绑定 [cite: 1]。
- 📷 **天文摄影助手**：输入焦距和像元尺寸，自动计算凌星时卫星的等效像素大小 [cite: 1]。
- 🎯 **灵活搜索模式**：支持“精确点搜索”和“区域范围搜索（反向推导周边可视范围）” [cite: 1]。
- 🎞️ **凌星视角模拟器**：专属小窗动态演算天顶方向、卫星进入与穿出天体盘面的相对轨迹 [cite: 1]。
- 💾 **多格式导出**：一键导出事件列表为 CSV 报表或 `.ics` (iCal) 日历文件 [cite: 1]。
- 🎨 **多主题 UI**：内置专业暗色、明亮模式和深空蓝三种主题界面 [cite: 1]。

---

## 🛠️ Windows 环境下的 Python 依赖项详解 (Windows Dependencies)

本软件完全基于 Python 编写，在 Windows 环境下运行需要依赖系统自带的标准库以及特定的第三方扩展包 [cite: 1]。

### 1. 核心第三方依赖库（需通过 pip 安装）
程序能够实现高精度计算和地图交互，主要依赖以下库，请确保在运行前正确安装 [cite: 1]：

* **`skyfield` (>=1.45)**
  * **作用**：核心天体物理、星历解析与轨道计算引擎 [cite: 1]。负责载入 `de421.bsp` 星历、解析 TLE 轨道根数、生成地球、太阳、月球及卫星的三维坐标系 [cite: 1]。
* **`tkintermapview` (>=1.93)**
  * **作用**：GUI 界面中的交互式地图展示组件 [cite: 1]。负责调用开源瓦片地图，实现平移、缩放、右键打点以及绘制凌星红线走廊 [cite: 1]。
* **`numpy` (>=1.21.0)**
  * **作用**：底层数据处理与数学运算 [cite: 1]。由于逐秒比对轨道距离计算量极大，软件使用 NumPy 进行矩阵向量化加速（如计算 `haversine` 球面距离） [cite: 1]。
* **`timezonefinder` (>=6.0.0)**
  * **作用**：离线时区解析 [cite: 1]。通过在地图上点选的经纬度坐标，自动推算该观测点所属的时区，以正确显示“本地时间” [cite: 1]。
* **`requests` (>=2.28.0) & `urllib3` (>=1.26.0)**
  * **作用**：网络抓取与底层连接管理 [cite: 1]。负责从各大数据库（如 CelesTrak, SatNOGS）请求实时轨道数据，并内置了 SSL 证书忽略机制（`urllib3.disable_warnings`）以适应特殊的网络环境 [cite: 1]。

### 2. Python 标准库（自带，无需安装）
您的 Windows Python 环境自带以下运行本软件所需的库 [cite: 1]：
* `tkinter` & `ttk` (GUI 界面构建) [cite: 1]
* `math`, `datetime`, `json`, `csv`, `os`, `sys`, `threading` (多线程防卡顿), `zoneinfo` [cite: 1]

---

## 📦 Windows 系统的详细安装与部署 (Installation Guide)

针对 Windows 用户，请严格按照以下步骤配置开发/运行环境：

### 第一步：安装 Python 解释器
1. 前往 [Python 官方下载页面 (python.org)](https://www.python.org/downloads/windows/) 下载 Python 3.8 ~ 3.11 之间的 Windows 安装包（推荐 3.10）。
2. **【关键】** 在安装界面的底部，**务必勾选 `Add Python 3.x to PATH`**（将 Python 添加到系统环境变量）。
3. 采用默认的 "Install Now" 完成安装。

### 第二步：获取软件源码
将本仓库克隆到本地，或者直接下载 .py文件一个不包含中文字符的路径下（例如 `D:\Satellite-Predictor`）：


### 第三步：创建与激活虚拟环境（推荐）
为了不污染系统的 Python 环境，建议在软件目录下创建独立的虚拟环境。打开命令提示符 (CMD) 或 PowerShell，依次执行：
```cmd
python -m venv venv
```
激活该虚拟环境（每次运行前都需要执行此步）：
```cmd
venv\Scripts\activate
```
*(激活成功后，命令行路径的最前方会出现 `(venv)` 标识)*

### 第四步：一键安装第三方依赖库
在项目根目录下创建一个名为 `requirements.txt` 的文件，填入以下内容，然后执行：
```text
skyfield>=1.45
tkintermapview>=1.93
numpy>=1.21.0
timezonefinder>=6.0.0
requests>=2.28.0
urllib3>=1.26.0
```
安装命令：
```cmd
pip install -r requirements.txt
```
*(如果下载速度过慢，可添加国内镜像源，例如：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`)*

---

## 🚀 如何启动与使用 (How to Run & Use)

1. **启动程序**：
   在已经激活了虚拟环境的终端中，运行以下命令启动 GUI 界面：
   ```cmd
   python satellite_transit_predictor.py
   ```

2. **操作指引**：
   * **定位观测点**：在左侧输入地名点击“定位”，或在右侧地图上**右键点击**选择“设为观测点” [cite: 1]。
   * **设置时间范围**：输入起始与结束时间（系统默认填充未来 24 小时） [cite: 1]。
   * **输入摄影预设（选填）**：在左侧输入您的主镜焦距 (mm) 和相机像元尺寸 (µm)，以便预估画面占比 [cite: 1]。
   * **勾选目标与卫星**：勾选凌星对象（太阳、月球等）以及卫星分类（如“空间站”）。若网络受阻，可在“📁外部文件导入”选项卡指定本地的 `.txt` 轨道文件 [cite: 1]。
   * **开始搜索**：点击“开始高精度凌星搜索”。**注意：首次运行时，系统会在后台自动下载一份约 16MB 的星历表文件 `de421.bsp`，请耐心等待（仅首次需下载）** [cite: 1]。
   * **查看结果**：计算完成后，点击右下角数据表的某一行，地图会绘制红色的中心线轨迹。点击顶部的“🛰️ 凌星视角模拟”按钮可查看相对运动轨迹小窗 [cite: 1]。

---

## ❓ 常见问题与简易解决方案 (Troubleshooting)

| 问题现象 | 可能的原因 | Windows 简易解决方案 |
| :--- | :--- | :--- |
| **首次启动卡在“正在初始化天体物理核心”** | 网络状况不佳，无法从服务器下载 `de421.bsp` 基础星历文件。 | 若长时间卡住，请直接在浏览器中搜索下载 `de421.bsp`，并将其复制到 `satellite_transit_predictor.py` 同级的文件夹目录下，重启软件即可跳过下载。 |
| **找不到任何卫星 / 提示网络异常** | 您的当地网络可能阻断了TLE 数据库。 | 1. 尝试在左侧“卫星来源”选项卡中切换为 `Ivan Stanojevic API` 或 `自动回退方案`。<br>2. 请自行通过浏览器下载 TLE 文件，在软件的“📁 外部文件导入”标签页中绑定即可实现纯离线计算 [cite: 1]。 |
| **预测结果表格始终为空** | 选定时间段内确实无事件，或搜索半径太小。 | 1. 尝试将“搜索模式”更改为 **“区域范围搜索”**，并调大搜索半径（如 50km 或 100km）。<br>2. 将结束时间延后 3 到 5 天 [cite: 1]。 |
| **启动时黑框一闪而过 / 报错 `ModuleNotFoundError`** | 依赖项未安装，或未在正确的虚拟环境中运行程序。 | 请重新打开 CMD，导航到代码目录，执行 `venv\Scripts\activate` 激活环境，然后确认是否已执行 `pip install -r requirements.txt`。 |
| **地图显示空白或无法加载** | `TkinterMapView` 请求底图超时。 | 确保网络通畅。如使用了系统代理 (VPN)，请尝试在 Windows 设置中关闭系统代理，或确保代理规则放行了 Python 进程。 |
