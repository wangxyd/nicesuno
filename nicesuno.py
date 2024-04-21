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
    version="1.0",
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
                logger.debug("[Nicesuno] no config.json, use config.json.template.")
                curdir = os.path.dirname(__file__)
                config_path = os.path.join(curdir, "config.json.template")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        conf = json.load(f)
            self.suno_api_bases = conf.get("suno_api_bases")
            self.music_create_prefixes = conf.get("music_create_prefixes")
            self.music_output_dir = conf.get("music_output_dir", "/tmp")
            if self.suno_api_bases and isinstance(self.suno_api_bases, List) \
                    and self.music_create_prefixes and isinstance(self.music_create_prefixes, List):
                logger.info("[Nicesuno] inited")
                self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
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
            # 判断是否包含创作音乐的前缀
            prefix = self._check_prefix(content, self.music_create_prefixes)
            if not prefix:
                logger.debug(f"[Nicesuno] not starts with music_create_prefixes, ignored. content={content}")
                return
            # 判断是否包含创作音乐的提示词
            suno_music_prompt = content[len(prefix):].strip()
            if not suno_music_prompt:
                logger.info("[Nicesuno] suno_music_prompt is empty, ignored.")
                return

            # 获取用户信息
            channel = e_context["channel"]
            actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
            to_user_nickname = context["msg"].to_user_nickname
            # 创作音乐
            data = self._suno_generate_music_with_description(suno_music_prompt)
            # 如果超过限额，则转换为创作歌词
            if data.get('detail') == 'Insufficient credits.':
                logger.warning(f"[Nicesuno] insufficient credits, changed to generating lyrics...")
                data = self._suno_generate_lyrics(suno_music_prompt)
                # 获取和发送歌词
                lid = data['id']
                logger.debug(f"[Nicesuno] start to handle lyrics, lid={lid}, data={data}")
                threading.Thread(target=self._handle_lyric, args=(channel, context, lid, suno_music_prompt)).start()
                # 发送写歌提醒
                reply = Reply(ReplyType.TEXT, f"Suno老师说一天只能唱10首歌😂哎呀，小伙伴们太捧场了！不过今天确实唱够了，{to_user_nickname}来为你写歌好不好😘")
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
                error = f"[Nicesuno] no clips in response data, data={data}"
                raise Exception(error)
        except Exception as e:
            # 发送失败提醒
            logger.warning(f"[Nicesuno] failed to generate music, error={e}")
            reply = Reply(ReplyType.TEXT, "抱歉！创作音乐失败，请稍后再试🥺")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    # 创作音乐
    def _suno_generate_music_with_description(self, suno_music_prompt):
        payload = {
            "gpt_description_prompt": suno_music_prompt,
            "make_instrumental": False,
            "mv": "chirp-v3-0",
        }
        response = requests.post(f"{self.suno_api_base}/generate/description-mode", data=json.dumps(payload), timeout=(5, 30))
        logger.debug(f"[Nicesuno] _suno_generate_music_with_description, response={response.text}")
        return response.json()

    # 获取音乐
    def _suno_get_music(self, aid):
        response = requests.get(f"{self.suno_api_base}/feed/{aid}", timeout=(5, 30))
        logger.debug(f"[Nicesuno] _suno_get_music, response={response.text}")
        return response.json()[0]

    # 创作歌词
    def _suno_generate_lyrics(self, suno_lyric_prompt):
        payload = {
            "prompt": suno_lyric_prompt
        }
        response = requests.post(f"{self.suno_api_base}/generate/lyrics/", data=json.dumps(payload), timeout=(5, 30))
        logger.debug(f"[Nicesuno] _suno_generate_lyrics, response={response.text}")
        return response.json()

    # 获取歌词
    def _suno_get_lyrics(self, lid):
        response = requests.get(f"{self.suno_api_base}/lyrics/{lid}", timeout=(5, 30))
        logger.debug(f"[Nicesuno] _suno_get_lyrics, response={response.text}")
        return response.json()

    # 下载和发送音乐
    def _handle_music(self, channel, context, aids: List):
        # 用户信息
        actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
        to_user_nickname = context["msg"].to_user_nickname
        # 获取歌词和音乐
        initial_delay_seconds = 15
        for aid in aids:
            # 检查音乐是否创作完成
            start_time = time.time()
            while True:
                if initial_delay_seconds:
                    time.sleep(initial_delay_seconds)
                    initial_delay_seconds = 0
                data = self._suno_get_music(aid)
                if data["audio_url"]:
                    break
                elif time.time() - start_time > 180:
                    raise TimeoutError("[Nicesuno] failed to get audio_url within 180 seconds.")
                time.sleep(5)
            # 获取歌曲信息
            title, metadata, audio_url = data["title"], data["metadata"], data["audio_url"]
            lyrics, tags, description_prompt = metadata["prompt"], metadata["tags"], metadata['gpt_description_prompt']
            # 发送歌词
            reply_text = f"🎻{title}🎻\n\n{lyrics}\n\n🎹风格: {tags}\n👶发起人：{actual_user_nickname}\n🍀制作人：Suno\n🎤提示词: {description_prompt}"
            logger.debug(f"[Nicesuno] 发送歌词，reply_text={reply_text}")
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
            # 检查封面是否创作完成
            while True:
                data = self._suno_get_music(aid)
                if data["image_url"]:
                    break
                elif time.time() - start_time > 60:
                    raise TimeoutError("[Nicesuno] failed to get image_url within 60 seconds.")
                time.sleep(5)
            # 发送封面
            image_url = data["image_url"]
            logger.debug(f"[Nicesuno] 发送封面，image_url={image_url}")
            reply = Reply(ReplyType.IMAGE_URL, image_url)
            channel.send(reply, context)
        # 获取视频地址
        video_urls = []
        for aid in aids:
            # 检查视频是否创作完成
            start_time = time.time()
            while True:
                data = self._suno_get_music(aid)
                if data["video_url"]:
                    video_urls.append(data["video_url"])
                    break
                elif time.time() - start_time > 180:
                    logger.warn(f"[Nicesuno] failed to get video_url within 180 seconds. aid={aid}, data={data}")
                time.sleep(10)
        # 查收提醒
        video_text = '\n'.join(f'视频{idx+1}: {url}' for idx, url in zip(range(len(video_urls)), video_urls))
        reply_text = f"{to_user_nickname}已经为您创作了音乐，请查收！以下是音乐视频：\n{video_text}"
        if context.get("isgroup", False):
            reply_text = f"@{actual_user_nickname}\n" + reply_text
        logger.debug(f"[Nicesuno] 发送查收提醒，reply_text={reply_text}")
        reply = Reply(ReplyType.TEXT, reply_text)
        channel.send(reply, context)

    # 下载和发送歌词
    def _handle_lyric(self, channel, context, lid, description_prompt=""):
        # 检查歌词是否创作完成
        start_time = time.time()
        while True:
            data = self._suno_get_lyrics(lid)
            if data["status"] == 'complete':
                break
            elif time.time() - start_time > 120:
                raise TimeoutError("[Nicesuno] Failed to get lyrics within 120 seconds.")
            time.sleep(5)
        # 发送歌词
        title, lyrics = data["title"], data["text"]
        actual_user_nickname = context["msg"].actual_user_nickname or context["msg"].other_user_nickname
        reply_text = f"🎻{title}🎻\n\n{lyrics}\n\n👶发起人：{actual_user_nickname}\n🍀制作人：Suno\n🎤提示词: {description_prompt}"
        logger.debug(f"[Nicesuno] 发送歌词，reply_text={reply_text}")
        reply = Reply(ReplyType.TEXT, reply_text)
        channel.send(reply, context)

    # 下载文件
    def _download_file(self, file_url, file_path):
        response = requests.get(file_url, allow_redirects=True, stream=True)
        if response.status_code != 200:
            raise Exception(f"[Nicesuno] 文件下载失败，file_url={file_url}, status_code={response.status_code}")
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

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
        return f'使用Suno创作音乐，输入唱+“提示词”调用该插件，例如“唱明天会更好”。'
