# Nicesuno

一款基于[Suno](https://suno.com/)和[Suno-API](https://github.com/SunoAI-API/Suno-API)创作音乐的chatgpt-on-wechat插件。

## 安装方法

**1. 浏览器访问[Suno](https://suno.com/)，获取当前账户的`session_id`和`Cookie`。**

+ 浏览器访问并登录Suno：https://suno.com/
+ 按下F12打开开发者工具，选择“网络”标签；
+ 刷新页面或者随便点击一下网页，找到类似`tokens?_clerk_js_version=4.72.0-snapshot.vc141245`或者`touch?_clerk_js_version=4.72.0-snapshot.vc141245`的请求，获取该Request URL中的`session_id`以及`Cookie`；
+ 比如Request URL为`https://clerk.suno.com/v1/client/sessions/sess_xeNbYcD4zOK89Vzwipl30x5gWq3/tokens?_clerk_js_version=4.72.0-snapshot.vc141245`，则`session_id`是`sess_xeNbYcD4zOK89Vzwipl30x5gWq3`。

**2. 部署SunoAI-API**

+ 详细的安装和配置步骤参考[Suno-API](https://github.com/SunoAI-API/Suno-API)，这里只给出大致步骤：
```shell
# 克隆代码
git clone https://github.com/SunoAI-API/Suno-API.git

# 配置Suno-API
cd Suno-API
cp .env.example .env
# 修改.env文件中的SESSION_ID和COOKIE两个环境变量，值分别为步骤1中获取的`session_id`和`Cookie`
vi .env
BASE_URL=https://studio-api.suno.ai
SESSION_ID=
COOKIE=

# 安装依赖
pip3 install -r requirements.txt

# 运行程序
nohup uvicorn main:app &>> Suno-API.log &

# 查看日志
tail -f Suno-API.log
```

**3. 安装Nicesuno插件**

```sh
#installp https://github.com/wangxyd/nicesuno.git
#scanp
```
+ 默认配置无需修改，即可使用Suno创作音乐。

## 自定义配置

+ 如果需要自定义配置，可以按照如下方法修改：
```shell
cp config.json.template config.json
vi config.json
{
  "suno_api_bases": ["http://127.0.0.1:8000"],
  "music_create_prefixes": ["唱"],
  "music_output_dir": "/tmp"
}
```

以上配置项中：

- `suno_api_bases`: Suno-API的监听地址和端口，注意该参数的值为一个字符串数组，后续用于实现自动切换Suno账号；
- `music_create_prefixes`: 创建音乐的消息前缀，注意该参数的值为一个字符串数组；
- `music_output_dir`: 音乐的存储目录，默认为`/tmp`。

有更好的想法或建议，欢迎积极提出哦~~~