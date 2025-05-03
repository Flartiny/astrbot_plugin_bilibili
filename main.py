import asyncio
import logging
import re
import os
import json
import traceback
from astrbot.api.event import CommandResult, AstrMessageEvent, MessageChain
from bilibili_api import user, Credential, video, bangumi
from astrbot.api.message_components import Image, Plain
from astrbot.api.event.filter import (
    command,
    regex,
    llm_tool,
    permission_type,
    PermissionType,
)
from bilibili_api.bangumi import IndexFilter as IF
from .constant import category_mapping
from astrbot.api.all import *
from typing import List, Optional
import PIL
from .utils import *

current_dir = os.path.dirname(__file__)
template_path = os.path.join(current_dir, "template.html")
logo_path = os.path.join(current_dir, "Astrbot.png")
with open(template_path, "r", encoding="utf-8") as file:
    HTML_TEMPLATE = file.read()

VALID_FILTER_TYPES = {"forward", "lottery", "video"}
DEFAULT_CFG = {
    "bili_sub_list": {}  # sub_user -> [{"uid": "uid", "last": "last_dynamic_id"}]
}
DATA_PATH = "data/astrbot_plugin_bilibili_test.json"
IMG_PATH = "data/temp.jpg"
BV = r"(?:\?.*)?(?:https?:\/\/)?(?:www\.)?bilibili\.com\/video\/(BV[\w\d]+)\/?(?:\?.*)?|BV[\w\d]+"
logger = logging.getLogger("astrbot")


@register("astrbot_plugin_bilibili", "Soulter", "", "", "")
class Main(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.cfg = config
        self.credential = None
        if not self.cfg["sessdata"]:
            logger.error(
                "bilibili 插件检测到没有设置 sessdata，请设置 bilibili sessdata。"
            )
        else:
            self.credential = Credential(self.cfg["sessdata"])
        self.interval_mins = float(self.cfg.get("interval_mins", 20))

        self.context = context
        self.rai = self.cfg.get("rai", True)

        if not os.path.exists(DATA_PATH):
            with open(DATA_PATH, "w", encoding="utf-8-sig") as f:
                f.write(json.dumps(DEFAULT_CFG, ensure_ascii=False, indent=4))
        with open(DATA_PATH, "r", encoding="utf-8-sig") as f:
            self.data = json.load(f)
        asyncio.create_task(self.dynamic_listener())

    @regex(BV)
    async def get_video_info(self, message: AstrMessageEvent):
        if len(message.message_str) == 12:
            bvid = message.message_str
        else:
            match_ = re.search(BV, message.message_str, re.IGNORECASE)
            if not match_:
                return
            bvid = "BV" + match_.group(1)[2:]

        v = video.Video(bvid=bvid)
        info = await v.get_info()
        online = await v.get_online()

        render_data = await create_render_data()
        render_data["name"] = "AstrBot"
        render_data["avatar"] = await image_to_base64(logo_path)
        render_data["title"] = info["title"]
        render_data["text"] = (
            f"UP 主: {info['owner']['name']}<br>"
            f"播放量: {info['stat']['view']}<br>"
            f"点赞: {info['stat']['like']}<br>"
            f"投币: {info['stat']['coin']}<br>"
            f"总共 {online['total']} 人正在观看"
        )
        render_data["image_urls"] = [info["pic"]]
        try:
            src = await self.html_render(HTML_TEMPLATE, render_data, False)
            await get_and_crop_image(src, IMG_PATH)
        finally:
            if os.path.exists(src):
                os.remove(src)
        await message.send(MessageChain().file_image(IMG_PATH))

    async def save_cfg(self):
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(self.data, ensure_ascii=False, indent=2))

    @command("订阅动态")
    async def dynamic_sub(self, message: AstrMessageEvent):
        input_text = message.message_str.strip()
        if "订阅动态" in input_text:
            input_text = input_text.replace("订阅动态", "", 1).strip()
        args = input_text.split(" ")
        uid = args[0]
        args.pop(0)

        filter_types: List[str] = []
        filter_regex: List[str] = []
        for arg in args:
            if arg in VALID_FILTER_TYPES:
                filter_types.append(arg)
            else:
                filter_regex.append(arg)

        sub_user = message.unified_msg_origin
        if not uid.isdigit():
            return CommandResult().message("UID 格式错误")

        # 检查是否已经存在该订阅
        if sub_user in self.data["bili_sub_list"] and any(
            sub["uid"] == int(uid) for sub in self.data["bili_sub_list"][sub_user]
        ):
            # 如果已存在，可以选择更新其过滤条件，或者提示用户先删除再添加
            return CommandResult().message(
                "该动态已订阅，如需修改过滤条件请先删除再重新订阅。"
            )  # 简化处理，也可以实现更新逻辑

        usr = user.User(int(uid), credential=self.credential)

        try:
            usr_info = await usr.get_user_info()
        except Exception as e:
            if "code" in e.args[0] and e.args[0]["code"] == -404:
                return CommandResult().message("啥都木有 (´;ω;`)")
            else:
                logger.error(traceback.format_exc())
                return CommandResult().message(f"获取 UP 主信息失败: {str(e)}")

        mid = usr_info["mid"]
        name = usr_info["name"]
        sex = usr_info["sex"]
        avatar = usr_info["face"]
        pendant = usr_info["pendant"]["image"]
        # sign = usr_info["sign"]
        # title = usr_info["official"]["title"]

        # 获取最新一条动态 (用于初始化 last_id)
        dyn_id = ""
        try:
            dyn = await usr.get_dynamics_new()
            # 构造新的订阅数据结构
            _sub_data = {
                "uid": int(uid),
                "last": "",
                "is_live": False,
                "filter_types": filter_types,
                "filter_regex": filter_regex,
            }
            _, dyn_id = await self.parse_last_dynamic(dyn, _sub_data)
            _sub_data["last"] = dyn_id  # 更新 last id
        except Exception as e:
            logger.error(f"获取 {name} 初始动态失败: {e}")

        # 保存配置
        if sub_user in self.data["bili_sub_list"]:
            self.data["bili_sub_list"][sub_user].append(_sub_data)
        else:
            self.data["bili_sub_list"][sub_user] = [_sub_data]
        await self.save_cfg()

        filter_desc = ""
        if filter_types:
            filter_desc += f"<br>过滤类型: {', '.join(filter_types)}"
        if filter_regex:
            filter_desc += f"<br>过滤正则: {filter_regex}"

        render_data = await create_render_data()
        render_data["name"] = "AstrBot"
        render_data["avatar"] = await image_to_base64(logo_path)
        render_data["pendant"] = pendant
        render_data["text"] = (
            f"📣 订阅成功！<br>"
            f"UP 主: {name} | 性别: {sex}"
            f"{filter_desc}"  # 显示过滤信息
        )
        render_data["image_urls"] = [avatar]
        render_data["url"] = f"https://space.bilibili.com/{mid}"
        render_data["qrcode"] = await create_qrcode(render_data["url"])
        try:
            logger.info(f"{render_data}")
            src = await self.html_render(HTML_TEMPLATE, render_data, False)
            await get_and_crop_image(src, IMG_PATH)
        finally:
            if os.path.exists(src):
                os.remove(src)
        await message.send(
            MessageChain().file_image(IMG_PATH).message(render_data["url"])
        )

    @command("订阅列表")
    async def sub_list(self, message: AstrMessageEvent):
        """查看 bilibili 动态监控列表"""
        sub_user = message.unified_msg_origin
        ret = """订阅列表：\n"""
        if sub_user in self.data["bili_sub_list"]:
            for idx, uid_sub_data in enumerate(self.data["bili_sub_list"][sub_user]):
                ret += f"{idx + 1}. {uid_sub_data['uid']}\n"
            return CommandResult().message(ret)
        else:
            return CommandResult().message("无订阅")

    @command("订阅删除")
    async def sub_del(self, message: AstrMessageEvent, uid: str):
        """删除 bilibili 动态监控"""
        sub_user = message.unified_msg_origin
        if sub_user in self.data["bili_sub_list"]:
            if len(uid) < 1:
                return CommandResult().message("参数数量不足。订阅动态 b站id")

            uid = int(uid)

            for idx, uid_sub_data in enumerate(self.data["bili_sub_list"][sub_user]):
                if uid_sub_data["uid"] == uid:
                    del self.data["bili_sub_list"][sub_user][idx]
                    await self.save_cfg()
                    return CommandResult().message("删除成功")
            return CommandResult().message("未找到指定的订阅")
        else:
            return CommandResult().message("您还没有订阅哦！")

    @llm_tool("get_bangumi")
    async def get_bangumi(
        self,
        message: AstrMessageEvent,
        style: str = "ALL",
        season: str = "ALL",
        start_year: int = None,
        end_year: int = None,
    ):
        """当用户希望推荐番剧时调用。根据用户的描述获取前 5 条推荐的动漫番剧。

        Args:
            style(string): 番剧的风格。默认为全部。可选值有：原创, 漫画改, 小说改, 游戏改, 特摄, 布袋戏, 热血, 穿越, 奇幻, 战斗, 搞笑, 日常, 科幻, 萌系, 治愈, 校园, 儿童, 泡面, 恋爱, 少女, 魔法, 冒险, 历史, 架空, 机战, 神魔, 声控, 运动, 励志, 音乐, 推理, 社团, 智斗, 催泪, 美食, 偶像, 乙女, 职场
            season(string): 番剧的季度。默认为全部。可选值有：WINTER, SPRING, SUMMER, AUTUMN。其也分别代表一月番、四月番、七月番、十月番
            start_year(number): 起始年份。默认为空，即不限制年份。
            end_year(number): 结束年份。默认为空，即不限制年份。
        """

        if style in category_mapping:
            style = getattr(IF.Style.Anime, category_mapping[style], IF.Style.Anime.ALL)
        else:
            style = IF.Style.Anime.ALL

        if season in ["WINTER", "SPRING", "SUMMER", "AUTUMN"]:
            season = getattr(IF.Season, season, IF.Season.ALL)
        else:
            season = IF.Season.ALL

        filters = bangumi.IndexFilterMeta.Anime(
            area=IF.Area.JAPAN,
            year=IF.make_time_filter(start=start_year, end=end_year, include_end=True),
            season=season,
            style=style,
        )
        index = await bangumi.get_index_info(
            filters=filters, order=IF.Order.SCORE, sort=IF.Sort.DESC, pn=1, ps=5
        )

        result = "推荐的番剧:\n"
        for item in index["list"]:
            result += f"标题: {item['title']}\n"
            result += f"副标题: {item['subTitle']}\n"
            result += f"评分: {item['score']}\n"
            result += f"集数: {item['index_show']}\n"
            result += f"链接: {item['link']}\n"
            result += "\n"
        result += "请分点，贴心地回答。不要输出 markdown 格式。"
        return result

    async def dynamic_listener(self):
        while True:
            await asyncio.sleep(60 * self.interval_mins)
            if self.credential is None:
                logger.warning("bilibili sessdata 未设置，无法获取动态")
                continue
            for sub_usr in self.data["bili_sub_list"]:
                # 遍历所有订阅的用户
                for idx, uid_sub_data in enumerate(self.data["bili_sub_list"][sub_usr]):
                    # 遍历用户订阅的UP
                    try:
                        usr = user.User(uid_sub_data["uid"], credential=self.credential)
                        dyn = await usr.get_dynamics_new()
                        lives = await usr.get_live_info()
                        if dyn is not None:
                            # 获取最新一条动态
                            # dyn_id - last
                            ret, dyn_id = await self.parse_last_dynamic(
                                dyn, uid_sub_data
                            )
                            if ret:
                                try:
                                    src = await self.html_render(HTML_TEMPLATE, ret, False)
                                    await get_and_crop_image(src, IMG_PATH)
                                finally:
                                    if os.path.exists(src):
                                        os.remove(src)
                                await self.context.send_message(
                                    sub_usr,
                                    MessageChain()
                                    .file_image(IMG_PATH)
                                    .message(ret["url"]),
                                )
                                self.data["bili_sub_list"][sub_usr][idx]["last"] = (
                                    dyn_id
                                )
                                await self.save_cfg()

                        if lives is not None:
                            # 获取直播间情况
                            is_live = self.data["bili_sub_list"][sub_usr][idx].get(
                                "is_live", False
                            )
                            live_room = (
                                lives.get("live_room", {})
                                or lives.get("live_room:", {})
                                or {}
                            )
                            live_name = live_room.get("title", "Unknown")
                            user_name = lives["name"]
                            cover_url = live_room.get("cover", "")
                            link = live_room.get("url", "Unknown")

                            render_data = await create_render_data()
                            render_data["name"] = "AstrBot"
                            render_data["avatar"] = await image_to_base64(logo_path)
                            render_data["title"] = live_name

                            if live_room.get("liveStatus", "") and not is_live:
                                render_data["text"] = (
                                    f"📣 你订阅的UP 「{user_name}」 开播了！"
                                )
                                render_data["url"] = link
                                render_data["image_urls"] = [cover_url]
                                self.data["bili_sub_list"][sub_usr][idx]["is_live"] = (
                                    True
                                )
                                await self.save_cfg()
                            if not live_room.get("liveStatus", "") and is_live:
                                render_data["text"] = (
                                    f"📣 你订阅的UP 「{user_name}」 下播了！"
                                )
                                render_data["url"] = link
                                render_data["image_urls"] = [cover_url]

                                self.data["bili_sub_list"][sub_usr][idx]["is_live"] = (
                                    False
                                )
                                await self.save_cfg()
                            if render_data["text"]:
                                render_data["qrcode"] = await create_qrcode(link)
                                try:
                                    src = await self.html_render(HTML_TEMPLATE, render_data, False)
                                    await get_and_crop_image(src, IMG_PATH)
                                finally:
                                    if os.path.exists(src):
                                        os.remove(src)
                                await self.context.send_message(
                                    sub_usr,
                                    MessageChain()
                                    .file_image(IMG_PATH)
                                    .message(render_data["url"]),
                                )

                    except Exception as e:
                        raise e

    @permission_type(PermissionType.ADMIN)
    @command("全局删除")
    async def global_sub(self, message: AstrMessageEvent, sid: str = None):
        """管理员指令。通过 SID 删除某一个群聊或者私聊的所有订阅。使用 /sid 查看当前会话的 SID。"""
        if not sid:
            return CommandResult().message(
                "通过 SID 删除某一个群聊或者私聊的所有订阅。使用 /sid 指令查看当前会话的 SID。"
            )

        candidate = []
        for sub_user in self.data["bili_sub_list"]:
            third = sub_user.split(":")[2]
            if third == str(sid) or sid == sub_user:
                candidate.append(sub_user)

        if not candidate:
            return CommandResult().message("未找到订阅")

        if len(candidate) == 1:
            self.data["bili_sub_list"].pop(candidate[0])
            await self.save_cfg()
            return CommandResult().message(f"删除 {sid} 订阅成功")

        return CommandResult().message("找到多个订阅者: " + ", ".join(candidate))

    @permission_type(PermissionType.ADMIN)
    @command("全局列表")
    async def global_list(self, message: AstrMessageEvent):
        """管理员指令。查看所有订阅者"""
        ret = "订阅会话列表：\n"

        if not self.data["bili_sub_list"]:
            return CommandResult().message("没有任何会话订阅过。")

        for sub_user in self.data["bili_sub_list"]:
            ret += f"- {sub_user}\n"
        return CommandResult().message(ret)

    async def parse_last_dynamic(self, dyn: dict, data: dict):
        uid, last = data["uid"], data["last"]
        filter_types = data.get("filter_types", [])
        filter_regex = data.get("filter_regex", [])
        items = dyn["items"]

        render_data = await create_render_data()

        for item in items:
            if "modules" not in item:
                continue
            # 过滤置顶
            if (
                "module_tag" in item["modules"]
                and "text" in item["modules"]["module_tag"]
                and item["modules"]["module_tag"]["text"] == "置顶"
            ):
                continue

            if item["id_str"] == last:
                # 无新动态
                return None, None

            dyn_id = item["id_str"]

            # 用户名称
            name = item["modules"]["module_author"]["name"]
            avatar = item["modules"]["module_author"].get("face")

            render_data["name"] = name
            render_data["avatar"] = avatar
            render_data["pendant"] = item["modules"]["module_author"]["pendant"][
                "image"
            ]
            # 转发类型
            if item["type"] == "DYNAMIC_TYPE_FORWARD":
                if "forward" in filter_types:
                    logger.info(f"转发类型在过滤列表 {filter_types} 中。")
                    return None, dyn_id  # 返回 None 表示不推送，但更新 dyn_id
                # todo
            # 投稿视频
            elif item["type"] == "DYNAMIC_TYPE_AV":
                if "video" in filter_types:
                    logger.info(f"视频类型在过滤列表 {filter_types} 中。")
                    return None, dyn_id
                archive = item["modules"]["module_dynamic"]["major"]["archive"]
                title = archive["title"]
                bv = archive["bvid"]
                cover_url = archive["cover"]

                try:
                    text = item["modules"]["module_dynamic"]["desc"]["text"]
                except (TypeError, KeyError):
                    text = None  # 或默认值

                if text:
                    text = await parse_rich_text(
                        item["modules"]["module_dynamic"]["desc"],
                        item["modules"]["module_dynamic"]["topic"],
                    )
                    render_data["text"] = f"投稿了新视频<br>{text}"
                else:
                    render_data["text"] = f"投稿了新视频<br>"
                render_data["title"] = title
                render_data["image_urls"] = [cover_url]
                url = f"https://www.bilibili.com/video/{bv}"
                render_data["qrcode"] = await create_qrcode(url)
                render_data["url"] = url
                logger.info(f"返回视频动态 {dyn_id}。")
                return render_data, dyn_id
            # 图文
            elif (
                item["type"] == "DYNAMIC_TYPE_DRAW"
                or item["type"] == "DYNAMIC_TYPE_WORD"
            ):
                opus = item["modules"]["module_dynamic"]["major"]["opus"]
                summary = opus["summary"]
                summary_text = summary["text"]
                jump_url = opus["jump_url"]
                topic = item["modules"]["module_dynamic"]["topic"]

                if filter_regex:  # 检查列表是否存在且不为空
                    for regex_pattern in filter_regex:
                        try:
                            if re.search(regex_pattern, summary_text):
                                logger.info(
                                    f"图文动态 {dyn_id} 的 summary 匹配正则 '{regex_pattern}'。"
                                )
                                return None, dyn_id  # 匹配到任意一个正则就返回
                        except re.error as e:
                            continue  # 如果正则表达式本身有误，跳过这个正则继续检查下一个

                if (
                    opus["summary"]["rich_text_nodes"][0].get("text") == "互动抽奖"
                    and "lottery" in filter_types
                ):
                    logger.info(f"互动抽奖在过滤列表 {filter_types} 中。")
                    return None, dyn_id

                render_data["text"] = await parse_rich_text(summary, topic)
                render_data["title"] = opus["title"]
                render_data["image_urls"] = [pic["url"] for pic in opus["pics"][:9]]
                url = f"https:{jump_url}"
                render_data["qrcode"] = await create_qrcode(url)
                render_data["url"] = url
                logger.info(f"返回图文动态 {dyn_id}。")
                return render_data, dyn_id

        return None, None
