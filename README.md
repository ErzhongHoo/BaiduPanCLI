# BaiduPan CLI

一个非官方的百度网盘命令行工具，为了**数据集拉取方便**而做。

本地上传带宽有限？把数据集传到百度网盘，然后在服务器上用这个工具直接拉下来。

## 功能

- 🔐 OAuth2.0 授权（支持无浏览器的服务器环境）
- 📂 浏览网盘文件列表
- 🔍 搜索文件
- ⬇️ 下载文件/文件夹（递归下载）
- ⬆️ 分片上传文件/文件夹（递归上传、失败重试、同尺寸跳过）
- 🔄 断点续传（中断后重新执行自动从断点继续）
- ⏭️ 自动跳过已下载的文件

## 安装

```bash
git clone git@github.com:ErzhongHoo/BaiduPanCLI.git
cd BaiduPanCLI
pip install -r requirements.txt
```

国内服务器建议使用清华镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置

复制 `.env.example` 为 `.env`，填入你的百度网盘开放平台应用密钥：

```bash
cp .env.example .env
```

```env
APP_KEY=你的AppKey
SECRET_KEY=你的SecretKey
SIGN_KEY=你的SignKey
```

密钥从 [百度网盘开放平台控制台](https://pan.baidu.com/union/console) 创建应用后获取。

## 快速开始

### 1. 授权

```bash
python baidupan.py auth
```

会输出一个授权链接，复制到本地浏览器打开，登录百度账号并授权后，页面会显示一个授权码，粘贴回终端即可。

Token 保存在 `~/.baidupan-cli/token.json`，refresh_token 有效期 10 年，基本只需授权一次。

### 2. 浏览文件

```bash
python baidupan.py ls /
python baidupan.py ls /Apps/MyDataset
```

### 3. 搜索

```bash
python baidupan.py search "train"
python baidupan.py search "imagenet" -p /Apps/MyDataset
```

### 4. 下载

```bash
# 下载单个文件
python baidupan.py download /Apps/MyDataset/data.zip -o ./data

# 下载整个文件夹（递归）
python baidupan.py download /Apps/MyDataset -o ./data
```

### 5. 上传

开放平台只允许应用写入 `/apps/<应用名>`。先运行 `ls /apps` 确认应用目录名。

```bash
# 创建目录
python baidupan.py mkdir /apps/你的应用名/3DHPSE-paper

# 上传单个文件
python baidupan.py upload ./model_best_iter4.pth.tar \
  /apps/你的应用名/3DHPSE-paper/model_best_iter4.pth.tar

# 递归上传目录，远端会保留本地目录名
python baidupan.py upload ./paper_release_20260812 \
  /apps/你的应用名
```

文件按 4 MiB 分片上传。再次执行相同命令时，远端同路径且同尺寸的文件会自动跳过；
需要覆盖时增加 `--overwrite`。

### 6. 查看用户信息

```bash
python baidupan.py info
```

## 典型使用场景

```
本地电脑                    百度网盘                    GPU 服务器
┌──────────┐   上传    ┌──────────────┐   拉取    ┌──────────────┐
│ 数据集    │ ───────> │ /Apps/xxx    │ <─────── │ baidupan.py  │
│ (大文件)  │  网页/客户端 │              │  本工具   │              │
└──────────┘          └──────────────┘          └──────────────┘
```

1. 在本地通过百度网盘客户端/网页上传数据集
2. 在服务器上用本工具拉取，利用服务器的大带宽快速下载

## 特性说明

### 断点续传

下载过程中如果中断（Ctrl+C、网络断开等），重新执行相同命令会自动从断点继续，不会重头开始。

### 自动跳过

已下载完成且文件大小一致的文件会自动跳过，适合批量下载时重复执行。

### Token 自动刷新

access_token 过期时自动使用 refresh_token 刷新，无需重新授权。

## 在服务器上授权

服务器没有浏览器，有两种方式完成授权：

**方式一：在服务器上操作**

1. 执行 `python baidupan.py auth`
2. 复制输出的链接到本地浏览器打开
3. 完成授权后把页面上的授权码粘贴回服务器终端

**方式二：复制 Token 文件**

1. 在本地电脑完成授权
2. 将 `~/.baidupan-cli/token.json` 复制到服务器相同路径

## 注意事项

- 百度网盘开放平台第三方应用只能向 `/apps/<应用名>` 写入文件
- 下载链接 (dlink) 有效期 8 小时，工具会在下载时实时获取
- 大文件下载速度受百度网盘开放平台限速影响

## 依赖

- Python 3.7+
- requests
- python-dotenv

## License

MIT
