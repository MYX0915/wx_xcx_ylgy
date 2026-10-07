# 旧版实验工具

本目录保留早期试验代码，不属于 `python run` 的运行链路，也不受主流程兼容性保证。当前正式流程从地图数据中的 `type` 求解，不依赖图像分类或 seed 解密。

| 文件 | 作用 | 状态 |
| --- | --- | --- |
| `board_vision.py` | 截图几何校准及图案模板比对 | 已退出当前主流程；需要 NumPy 和 Pillow |
| `decode_seed.py` | 解密 seed 响应并按洗牌算法给静态地图填类型 | 旧备用研究工具；需要 PyCryptodome 和对应地图、密钥、IV |
| `instrument_wechat_seed_package.py` | 为本地小程序包副本插入 seed 输出代码 | 旧实验工具；只应处理副本，不修改微信原始缓存 |
| `click_pipeline_checks.py` | 旧版截图/抓包回归检查 | 依赖未提交的本机样本文件，不属于默认测试套件 |

安装这些工具的额外依赖：

```sh
python3 -m pip install -r experiments/legacy/requirements.txt
```

这些实验脚本未纳入默认 CI 或主流程测试。不要把 `encryptKey`、IV、完整请求 URL、微信本地包或 Reqable 原始记录提交到仓库。
