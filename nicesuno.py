# encoding:utf-8
import os
import json
import time
import requests
import threading
from typing import List
from pathvalidate import sanitize_filename

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *

@plugins.register(
    name="Nicesuno",
    desire_priority=90,
    hidden=False,
    desc="一款基于Suno和Suno-API创作音乐的插件。",
    version="1.2",
    author="空心菜",
)
class Nicesuno(Plugin):
    def __init__(self):
        super().__init__()
        try:
            # 加载配置
            conf = super().load_config()
            # 配置不存在则使用默认配置
            if not conf:
                logger.debug("[Nicesuno] config.json not found, config.json.template used.")
                curdir = os.path.dirname(__file__)
                config_path = os.path.join(curdir, "config.json.template")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        conf = json.load(f)
            self.suno_api_bases = conf.get("suno_api_bases", [])
            self.music_create_prefixes = conf.get("music_create_prefixes", [])
            self.instrumental_create_prefixes = conf.get("instrumental_create_prefixes", [])
            self.music_output_dir = conf.get("music_output_dir", "/tmp")
            self.is_send_lyrics = conf.get("is_send_lyrics", True)
            self.is_send_covers = conf.get("is_send_covers", True)
            if not os.path.exists(self.music_output_dir):
                logger.info(f"[Nicesuno] music_output_dir={self.music_output_dir} not exists, create it.")
                os.makedirs(self.music_output_dir)
            if self.suno_api_bases and isinstance(self.suno_api_bases, List) \
                    and self.music_create_prefixes and isinstance(self.music_create_prefixes, List):
                self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
                logger.info("[Nicesuno] inited")
            else:
                logger.warn("[Nicesuno] init failed because suno_api_bases or music_create_prefixes is incorrect.")
            # 待实现：部署多套Suno-API，实现限额后自动切换Suno账号
            self.suno_api_base = self.suno_api_bases[0]
        except Exception as e:
            logger.error(f"[Nicesuno] init failed, ignored.")
            raise e

    def on_handle_context(self, e_context: EventContext):
        try:
            # 判断是否是TEXT类型消息
            context = e_context["context"]
            if context.type != ContextType.TEXT:
                return
            content = context.content
            logger.debug(f"[Nicesuno] on_handle_context. content={content}")

            # 判断是否包含创作声乐/器乐的前缀
            make_instrumental = False
            music_create_prefix = self._check_prefix(content, self.music_create_prefixes)
            instrumental_create_prefix = self._check_prefix(content, self.instrumental_create_prefixes)
            if music_create_prefix:
                suno_music_prompt = content[len(music_create_prefix):].strip()
            elif instrumental_create_prefix:
                make_instrumental = True
                suno_music_prompt = content[len(instrumental_create_prefix):].strip()
            else:
                logger.debug(f"[Nicesuno] content not starts with music_create_prefixes or instrumental_create_prefixes, ignored.")
                return

            # 判断是否包含创作音乐的提示词
            if not suno_music_prompt:
                logger.info("[Nicesuno] suno_music_prompt is empty, ignored.")
                return

            # 获取用户信息
            channel = e_context["channel"]
            actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
            to_user_nickname = context["msg"].to_user_nickname

            # 创作音乐
            logger.info(f"[Nicesuno] start generating, suno_music_prompt={suno_music_prompt}, make_instrumental={make_instrumental}.")
            data = self._suno_generate_music_with_description(suno_music_prompt, make_instrumental=make_instrumental)
            if not data:
                error = f"response data of _suno_generate_music_with_description is empty."
                raise Exception(error)
            # 如果Suno-API的Token失效
            elif data.get('detail') == 'Unauthorized':
                logger.warning(f"[Nicesuno] unauthorized, please check Suno-API...")
                reply = Reply(ReplyType.TEXT, f"因为长期翘课，被Suno老师劝退了😂请重新找Suno老师申请入学...")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            # 如果Suno超过限额，则转换为创作歌词
            elif data.get('detail') == 'Insufficient credits.':
                logger.warning(f"[Nicesuno] insufficient credits, changed to generating lyrics...")
                data = self._suno_generate_lyrics(suno_music_prompt)
                if not data:
                    error = f"response data of _suno_generate_lyrics is empty."
                    raise Exception(error)
                # 获取和发送歌词
                lid = data['id']
                logger.debug(f"[Nicesuno] start to handle lyrics, lid={lid}, data={data}")
                threading.Thread(target=self._handle_lyric, args=(channel, context, lid, suno_music_prompt)).start()
                # 发送写歌提醒
                reply = Reply(ReplyType.TEXT, f"Suno老师说一天只能创作5次😂今天确实唱够了，{to_user_nickname}来为你写歌好不好😘")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            # 获取和发送音乐
            elif data.get('clips'):
                aids = [clip['id'] for clip in data['clips']]
                logger.debug(f"[Nicesuno] start to handle music, aids={aids}, data={data}")
                threading.Thread(target=self._handle_music, args=(channel, context, aids)).start()
                # 发送请稍等提醒
                reply = Reply(ReplyType.TEXT, f"{actual_user_nickname}，{to_user_nickname}正在为您创作音乐，请稍等☕")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
            else:
                error = f"no clips in response data, data={data}"
                raise Exception(error)
        except Exception as e:
            # 发送失败提醒
            logger.warning(f"[Nicesuno] failed to generate music, error={e}")
            reply = Reply(ReplyType.TEXT, "抱歉！创作音乐失败，请稍后再试🥺")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    # 创作音乐
    def _suno_generate_music_with_description(self, suno_music_prompt, make_instrumental=False, retry_count=0):
        payload = {
            "gpt_description_prompt": suno_music_prompt,
            "make_instrumental": make_instrumental,
            "mv": "chirp-v3-0",
        }
        while retry_count >= 0:
            try:
                response = requests.post(f"{self.suno_api_base}/generate/description-mode", data=json.dumps(payload), timeout=(5, 30))
                if response.status_code != 200:
                    raise Exception(f"status_code is not ok, status_code={response.status_code}")
                logger.debug(f"[Nicesuno] _suno_generate_music_with_description, response={response.text}")
                return response.json()
            except Exception as e:
                logger.error(f"[Nicesuno] _suno_generate_music_with_description failed, suno_music_prompt={suno_music_prompt}, error={e}")
                retry_count -= 1
                time.sleep(5)

    # 获取音乐信息
    def _suno_get_music(self, aid, retry_count=3):
        while retry_count >= 0:
            try:
                response = requests.get(f"{self.suno_api_base}/feed/{aid}", timeout=(5, 30))
                if response.status_code != 200:
                    raise Exception(f"status_code is not ok, status_code={response.status_code}")
                logger.debug(f"[Nicesuno] _suno_get_music, response={response.text}")
                return response.json()[0]
            except Exception as e:
                logger.error(f"[Nicesuno] _suno_get_music failed, aid={aid}, error={e}")
                retry_count -= 1
                time.sleep(5)

    # 创作歌词
    def _suno_generate_lyrics(self, suno_lyric_prompt, retry_count=3):
        payload = {
            "prompt": suno_lyric_prompt
        }
        while retry_count >= 0:
            try:
                response = requests.post(f"{self.suno_api_base}/generate/lyrics/", data=json.dumps(payload), timeout=(5, 30))
                if response.status_code != 200:
                    raise Exception(f"status_code is not ok, status_code={response.status_code}")
                logger.debug(f"[Nicesuno] _suno_generate_lyrics, response={response.text}")
                return response.json()
            except Exception as e:
                logger.error(f"[Nicesuno] _suno_generate_lyrics failed, suno_lyric_prompt={suno_lyric_prompt}, error={e}")
                retry_count -= 1
                time.sleep(5)

    # 获取歌词信息
    def _suno_get_lyrics(self, lid, retry_count=3):
        while retry_count >= 0:
            try:
                response = requests.get(f"{self.suno_api_base}/lyrics/{lid}", timeout=(5, 30))
                if response.status_code != 200:
                    raise Exception(f"status_code is not ok, status_code={response.status_code}")
                logger.debug(f"[Nicesuno] _suno_get_lyrics, response={response.text}")
                return response.json()
            except Exception as e:
                logger.error(f"[Nicesuno] _suno_get_lyrics failed, lid={lid}, error={e}")
                retry_count -= 1
                time.sleep(5)

    # 下载和发送音乐
    def _handle_music(self, channel, context, aids: List):
        # 用户信息
        actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
        to_user_nickname = context["msg"].to_user_nickname
        # 获取歌词和音乐
        initial_delay_seconds = 15
        last_lyrics = ""
        for aid in aids:
            # 获取音乐信息
            start_time = time.time()
            while True:
                if initial_delay_seconds:
                    time.sleep(initial_delay_seconds)
                    initial_delay_seconds = 0
                data = self._suno_get_music(aid)
                if not data:
                    raise Exception("[Nicesuno] 获取音乐信息失败！")
                elif data["audio_url"]:
                    break
                elif time.time() - start_time > 180:
                    raise TimeoutError("[Nicesuno] 获取音乐信息超时！")
                time.sleep(5)
            # 解析音乐信息
            title, metadata, audio_url = data["title"], data["metadata"], data["audio_url"]
            lyrics, tags, description_prompt = metadata["prompt"], metadata["tags"], metadata['gpt_description_prompt']
            # 发送歌词
            if not self.is_send_lyrics:
                logger.debug(f"[Nicesuno] 发送歌词开关关闭，不发送歌词！")
            elif lyrics == last_lyrics:
                logger.debug("[Nicesuno] 歌词和上次相同，不再重复发送歌词！")
            else:
                reply_text = f"🎻{title}🎻\n\n{lyrics}\n\n🎹风格: {tags}\n👶发起人：{actual_user_nickname}\n🍀制作人：Suno\n🎤提示词: {description_prompt}"
                logger.debug(f"[Nicesuno] 发送歌词，reply_text={reply_text}")
                last_lyrics = lyrics
                reply = Reply(ReplyType.TEXT, reply_text)
                channel.send(reply, context)
            # 下载音乐
            filename = f"{int(time.time())}_{sanitize_filename(description_prompt).replace(' ', '')[:20]}"
            audio_path = os.path.join(self.music_output_dir, f"{filename}.mp3")
            logger.debug(f"[Nicesuno] 下载音乐，audio_url={audio_url}")
            self._download_file(audio_url, audio_path)
            # 发送音乐
            logger.debug(f"[Nicesuno] 发送音乐，audio_path={audio_path}")
            reply = Reply(ReplyType.FILE, audio_path)
            channel.send(reply, context)
            # 发送封面
            if not self.is_send_covers:
                logger.debug(f"[Nicesuno] 发送封面开关关闭，不发送封面！")
            else:
                # 获取封面信息
                start_time = time.time()
                while True:
                    data = self._suno_get_music(aid)
                    if not data:
                        #raise Exception("[Nicesuno] 获取封面信息失败！")
                        logger.warning("[Nicesuno] 获取封面信息失败！")
                        break
                    elif data["image_url"]:
                        break
                    elif time.time() - start_time > 60:
                        #raise TimeoutError("[Nicesuno] 获取封面信息超时！")
                        logger.warning("[Nicesuno] 获取封面信息超时！")
                        break
                    time.sleep(5)
                if data and data["image_url"]:
                    image_url = data["image_url"]
                    logger.debug(f"[Nicesuno] 发送封面，image_url={image_url}")
                    reply = Reply(ReplyType.IMAGE_URL, image_url)
                    channel.send(reply, context)
                else:
                    logger.warning(f"[Nicesuno] 获取封面信息失败，放弃发送封面！")
        # 获取视频地址
        video_urls = []
        for aid in aids:
            # 获取视频地址
            start_time = time.time()
            while True:
                data = self._suno_get_music(aid)
                if not data:
                    #raise Exception("[Nicesuno] 获取视频地址失败！")
                    logger.warning("[Nicesuno] 获取视频地址失败！")
                    video_urls.append("获取失败！")
                    break
                elif data["video_url"]:
                    video_urls.append(data["video_url"])
                    break
                elif time.time() - start_time > 180:
                    #raise TimeoutError("[Nicesuno] 获取视频地址超时！")
                    logger.warning("[Nicesuno] 获取视频地址超时！")
                    video_urls.append("获取超时！")
                time.sleep(10)
        # 查收提醒
        video_text = '\n'.join(f'视频{idx+1}: {url}' for idx, url in zip(range(len(video_urls)), video_urls))
        reply_text = f"{to_user_nickname}已经为您创作了音乐，请查收！以下是音乐视频：\n{video_text}"
        if context.get("isgroup", False):
            reply_text = f"@{actual_user_nickname}\n" + reply_text
        logger.debug(f"[Nicesuno] 发送查收提醒，reply_text={reply_text}")
        reply = Reply(ReplyType.TEXT, reply_text)
        channel.send(reply, context)

    # 获取和发送歌词
    def _handle_lyric(self, channel, context, lid, description_prompt=""):
        # 获取歌词信息
        start_time = time.time()
        while True:
            data = self._suno_get_lyrics(lid)
            if not data:
                raise Exception("[Nicesuno] 获取歌词信息失败！")
            elif data["status"] == 'complete':
                break
            elif time.time() - start_time > 120:
                raise TimeoutError("[Nicesuno] 获取歌词信息超时！")
            time.sleep(5)
        # 发送歌词
        title, lyrics = data["title"], data["text"]
        actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
        reply_text = f"🎻{title}🎻\n\n{lyrics}\n\n👶发起人：{actual_user_nickname}\n🍀制作人：Suno\n🎤提示词: {description_prompt}"
        logger.debug(f"[Nicesuno] 发送歌词，reply_text={reply_text}")
        reply = Reply(ReplyType.TEXT, reply_text)
        channel.send(reply, context)

    # 下载文件
    def _download_file(self, file_url, file_path, retry_count=3):
        while retry_count >= 0:
            try:
                response = requests.get(file_url, allow_redirects=True, stream=True)
                if response.status_code != 200:
                    raise Exception(f"[Nicesuno] 文件下载失败，file_url={file_url}, status_code={response.status_code}")
                with open(file_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            f.write(chunk)
            except Exception as e:
                logger.error(f"[Nicesuno] 文件下载失败，file_url={file_url}, error={e}")
                retry_count -= 1
                time.sleep(5)
            else:
                break
    # 检查是否包含创作音乐的前缀
    def _check_prefix(self, content, prefix_list):
        if not prefix_list:
            return None
        for prefix in prefix_list:
            if content.startswith(prefix):
                return prefix
        return None

    # 帮助文档
    def get_help_text(self, **kwargs):
        return '使用Suno创作音乐：\n唱/演唱+“提示词”：创作声乐；\n演奏+“提示词”：创作器乐；\n例如：唱明天会更好。'
