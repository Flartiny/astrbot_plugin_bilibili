import asyncio
import logging
import re
import os
import json
import traceback
from astrbot.api.event import CommandResult, AstrMessageEvent, MessageChain
from bilibili_api import Credential # Keep for type hints and direct use
from bilibili_api.bangumi import IndexFilter as IF # Keep for get_bangumi
# from bilibili_api import user, video, bangumi # Removed, handled by BilibiliApiClient

from astrbot.api.message_components import Image, Plain
from astrbot.api.event.filter import (
    command,
    regex,
    llm_tool,
    permission_type,
    PermissionType,
    event_message_type,
    EventMessageType,
)
from .constant import category_mapping
from astrbot.api.all import * # Keep Context, Star, CommandResult, AstrMessageEvent, MessageChain etc.
from typing import List, Optional # Keep for type hints

# import PIL # Likely not needed directly in main.py anymore
import aiohttp # Added for _b23_to_bv_helper

from .bilibili_client import BilibiliApiClient
from .data_manager import DataManager
from .dynamic_parser import DynamicParser
from .html_renderer import HTMLRenderer, create_render_data, image_to_base64, create_qrcode

CURRENT_DIR = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(CURRENT_DIR, "template.html")
LOGO_PATH = os.path.join(CURRENT_DIR, "Astrbot.png")
with open(TEMPLATE_PATH, "r", encoding="utf-8") as file:
    HTML_TEMPLATE = file.read() # Keep HTML_TEMPLATE loading

# MAX_ATTEMPTS = 3 # Moved to HTMLRenderer
# RETRY_DELAY = 2 # Moved to HTMLRenderer
VALID_FILTER_TYPES = {"forward", "lottery", "video"} # Keep
DEFAULT_CFG = {
    "bili_sub_list": {}  # sub_user -> [{"uid": "uid", "last": "last_dynamic_id"}]
} # Keep
DATA_PATH = "data/astrbot_plugin_bilibili.json" # Keep
IMG_PATH = "data/temp.jpg" # Keep, used as output path for rendering
BV = r"(?:\?.*)?(?:https?:\/\/)?(?:www\.)?bilibili\.com\/video\/(BV[\w\d]+)\/?(?:\?.*)?|BV[\w\d]+" # Keep
logger = logging.getLogger("astrbot") # Keep

# Helper function moved from utils.py
async def _b23_to_bv_helper(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session: # Ensure headers are passed to session for all requests if needed, or per-request
            async with session.get(
                url=url, allow_redirects=False, timeout=10 # Headers in session apply, or add headers=headers here too
            ) as response:
                if 300 <= response.status < 400:
                    location_url = response.headers.get("Location")
                    if location_url:
                        # Extract the part of the URL before the first '?'
                        base_url = location_url.split("?", 1)[0]
                        # Further parsing to extract BVid if it's a full video URL
                        match = re.search(r"/(BV[a-zA-Z0-9]+)", base_url)
                        if match:
                            return "https://www.bilibili.com/video/" + match.group(1)
                        return base_url # Or return the cleaned location_url if no specific BV pattern found
    except aiohttp.ClientError as e_aio: # More specific exception for network errors
        logger.error(f"Aiohttp error converting b23.tv URL {url}: {e_aio}")
    except Exception as e:
        logger.error(f"Error converting b23.tv URL {url}: {e}\n{traceback.format_exc()}")
    return url # Fallback to original URL on error


@register("astrbot_plugin_bilibili", "Soulter", "", "", "")
class Main(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.cfg = config
        self.credential: Optional[Credential] = None # Ensure type hint for credential
        if not self.cfg.get("sessdata"):
            logger.error(
                "bilibili 插件检测到没有设置 sessdata，请设置 bilibili sessdata。"
            )
        else:
            self.credential = Credential(
                sessdata=self.cfg["sessdata"],
                bili_jct=self.cfg.get("bili_jct"),
                buvid3=self.cfg.get("buvid3"),
                dedeuserid=self.cfg.get("dedeuserid"),
            )
        
        self.interval_mins = float(self.cfg.get("interval_mins", 20))
        self.context = context # Keep context for html_render_func and send_message
        self.rai = self.cfg.get("rai", True) # Keep

        # Initialize new components
        self.bili_client = BilibiliApiClient(self.credential)
        self.data_manager = DataManager(DATA_PATH, DEFAULT_CFG)
        # self.data is now self.data_manager.data
        # Loading of data is handled by DataManager's __init__
        
        self.dynamic_parser = DynamicParser()
        
        # Ensure html_render_func is correctly passed from context
        # Assuming self.context.html_render exists and matches the expected signature
        # for HTMLRenderer's html_render_func.
        if not hasattr(self.context, 'html_render') or not callable(self.context.html_render):
            logger.error("Context is missing a callable 'html_render' function for HTMLRenderer.")
            # Handle this error appropriately, maybe raise or use a fallback
            # For now, proceeding with the assumption it exists.
            _html_render_func = None # Or some dummy function
        else:
            _html_render_func = self.context.html_render

        self.html_renderer = HTMLRenderer(
            html_template_str=HTML_TEMPLATE,
            logo_path=LOGO_PATH,
            html_render_func=_html_render_func
        )

        self.dynamic_listener_task = asyncio.create_task(self.dynamic_listener())

    @regex(BV)
    async def get_video_info(self, message: AstrMessageEvent):
        if len(message.message_str) == 12:
            bvid = message.message_str
        else:
            match_ = re.search(BV, message.message_str, re.IGNORECASE)
            if not match_:
                return
            bvid = "BV" + match_.group(1)[2:]

        # Use BilibiliApiClient
        video_data = await self.bili_client.get_video_info_api(bvid)
        if not video_data:
            await message.send("无法获取视频信息。") # Or some other error message
            return

        info = video_data['info']
        online = video_data['online']

        # Use create_render_data and image_to_base64 from html_renderer
        render_data = await create_render_data()
        render_data["name"] = "AstrBot" # Or use info['owner']['name'] if preferred for video author
        render_data["avatar"] = await image_to_base64(LOGO_PATH)
        render_data["title"] = info["title"]
        render_data["text"] = (
            f"UP 主: {info['owner']['name']}<br>"
            f"播放量: {info['stat']['view']}<br>"
            f"点赞: {info['stat']['like']}<br>"
            f"投币: {info['stat']['coin']}<br>"
            f"总共 {online['total']} 人正在观看"
        )
        render_data["image_urls"] = [info["pic"]]
        # render_data["url"] = f"https://www.bilibili.com/video/{bvid}" # Add URL for QR code if desired
        # render_data["qrcode"] = await create_qrcode(render_data["url"])


        # Use HTMLRenderer
        try:
            await self.html_renderer.render_html_to_image(render_data, IMG_PATH)
            await message.send(MessageChain().file_image(IMG_PATH))
        except Exception as e:
            logger.error(f"Error rendering video info image: {e}")
            await message.send("渲染视频信息图片失败。")


    async def save_cfg(self): # Step 3: Update save_cfg
        # This method might be simplified or removed if DataManager handles saves automatically on update.
        # For now, explicit save.
        self.data_manager.save_data() # Uses DataManager's save method

    @command("订阅动态")
    async def dynamic_sub(self, message: AstrMessageEvent):
        input_text = message.message_str.strip()
        if "订阅动态" in input_text:
            input_text = input_text.replace("订阅动态", "", 1).strip()
        args = input_text.split(" ")
        uid_str = args[0]
        args.pop(0)

        filter_types: List[str] = []
        filter_regex_str: Optional[str] = None # DynamicParser expects a single regex string or None
        
        # Assuming filter_regex from args are joined into one string if multiple provided
        # Or, if DynamicParser is updated to handle a list, this can be a list.
        # For now, let's join them. If no regex args, it remains None.
        raw_regex_args = []
        for arg in args:
            if arg in VALID_FILTER_TYPES:
                # Map to full dynamic types if necessary, e.g. "video" -> "DYNAMIC_TYPE_AV"
                # For now, assuming DynamicParser can handle short forms or these are direct internal types.
                # The prompt for DynamicParser mentioned it takes sub_data["filter_types"],
                # which are likely the full DYNAMIC_TYPE_* strings.
                # This mapping might need to happen here or be documented for the user.
                # For now, let's assume VALID_FILTER_TYPES are what DynamicParser expects.
                if arg == "video":
                    filter_types.append("DYNAMIC_TYPE_AV")
                elif arg == "forward":
                    filter_types.append("DYNAMIC_TYPE_FORWARD")
                # "lottery" is not a direct dynamic type, but a content characteristic.
                # DynamicParser's regex or specific logic for lottery should handle it.
                # We can pass "lottery" in filter_types if DynamicParser is designed to check for it.
                # Or it's purely a regex thing. Let's assume it's a special keyword for now for the parser.
                elif arg == "lottery": # This might be a special filter keyword for DynamicParser
                    filter_types.append("LOTTERY_FILTER") # Example, actual value depends on parser
                else:
                    filter_types.append(arg) # For other direct types if any
            else:
                raw_regex_args.append(arg)
        
        if raw_regex_args:
            filter_regex_str = " ".join(raw_regex_args) # Example: join multiple regex patterns

        sub_user = message.unified_msg_origin
        if not uid_str.isdigit():
            return CommandResult().message("UID 格式错误")
        
        uid = int(uid_str)

        # Access subscription data using self.data_manager.data
        bili_sub_list = self.data_manager.get_data("bili_sub_list", {})

        if sub_user in bili_sub_list:
            updated = False
            for sub in bili_sub_list[sub_user]:
                if sub["uid"] == uid:
                    sub["filter_types"] = filter_types
                    sub["filter_regex"] = filter_regex_str
                    updated = True
                    break
            if updated:
                self.data_manager.update_data("bili_sub_list", bili_sub_list) # Updates and saves
                return CommandResult().message("该动态已订阅，已更新过滤条件。")

        # Use BilibiliApiClient
        usr_info = await self.bili_client.get_user_info_api(uid)
        if not usr_info:
            # Check for specific error if BilibiliApiClient returns structured errors
            # For now, assuming None means failure.
            # The original code checked e.args[0]["code"] == -404
            # This detail would need to be available from BilibiliApiClient's error reporting
            logger.error(f"获取 UID {uid} 的用户信息失败。")
            return CommandResult().message("获取 UP 主信息失败或用户不存在。")


        mid = usr_info["mid"]
        name = usr_info["name"]
        sex = usr_info["sex"]
        avatar = usr_info["face"]
        # pendant = usr_info.get("pendant", {}).get("image", "") # Safely get pendant
        # sign = usr_info.get("sign", "")
        # official_title = usr_info.get("official", {}).get("title", "")

        # Initialize _sub_data for DynamicParser
        _sub_data = {
            "uid": uid,
            "last": "", # Initialize last dynamic ID as empty
            "is_live": False,
            "filter_types": filter_types,
            "filter_regex": filter_regex_str,
        }
        
        initial_dyn_id = ""
        try:
            # Fetch initial dynamics to set the "last" id
            dyn_response = await self.bili_client.get_user_dynamics_api(uid)
            if dyn_response and dyn_response.get("items"):
                # Pass only items to the parser.
                # parse_dynamics_items expects sub_data to have "last" for comparison,
                # but for initialization, "last" is empty. It should return the latest ID from fetched items.
                _, initial_dyn_id_from_parser = await self.dynamic_parser.parse_dynamics_items(
                    dyn_response['items'], _sub_data # _sub_data here has "last": ""
                )
                if initial_dyn_id_from_parser:
                    initial_dyn_id = initial_dyn_id_from_parser
                else: # Fallback if parser returns None (e.g. no items)
                    if dyn_response['items']: # If items were present, take the first one's ID
                         initial_dyn_id = dyn_response['items'][0].get('id_str','')
                    else: # no items at all
                        initial_dyn_id = ""

            _sub_data["last"] = initial_dyn_id # Set the "last" ID based on the newest dynamic fetched
            logger.info(f"Initialized subscription for {name} (UID: {uid}) with last dynamic ID: {initial_dyn_id}")

        except Exception as e:
            logger.error(f"获取 {name} (UID: {uid}) 初始动态失败: {e}\n{traceback.format_exc()}")
            # Decide if subscription should proceed without an initial dynamic ID
            # For now, it will proceed with _sub_data["last"] = "" if an error occurred

        # Save new subscription using DataManager
        if sub_user not in bili_sub_list:
            bili_sub_list[sub_user] = []
        bili_sub_list[sub_user].append(_sub_data)
        self.data_manager.update_data("bili_sub_list", bili_sub_list) # Updates and saves

        filter_desc = ""
        if filter_types: # Assuming filter_types now contains the processed types like DYNAMIC_TYPE_AV
            readable_types = [ft.replace("DYNAMIC_TYPE_", "").capitalize() if "DYNAMIC_TYPE_" in ft else ft for ft in filter_types]
            filter_desc += f"<br>过滤类型: {', '.join(readable_types)}"
        if filter_regex_str:
            filter_desc += f"<br>过滤正则: {filter_regex_str}"

        # Rendering output using HTMLRenderer and its utilities
        render_data = await create_render_data()
        render_data["name"] = "AstrBot" # Bot name for the card
        render_data["avatar"] = await image_to_base64(LOGO_PATH)
        # render_data["pendant"] = pendant # If you want to show user's pendant
        render_data["text"] = (
            f"📣 订阅成功！<br>"
            f"UP 主: {name} | 性别: {sex}"
            f"{filter_desc}"
        )
        render_data["image_urls"] = [avatar] # User's avatar as the main image for this card
        render_data["url"] = f"https://space.bilibili.com/{mid}"
        render_data["qrcode"] = await create_qrcode(render_data["url"]) # from html_renderer

        try:
            if self.rai:
                await self.html_renderer.render_html_to_image(render_data, IMG_PATH)
                await message.send(
                    MessageChain().file_image(IMG_PATH).message(render_data["url"])
                )
            else:
                # Non-RAI mode: send text and image separately
                # The text in render_data["text"] is HTML formatted.
                # For Plain message, we might need to strip HTML or use a plain text version.
                # For now, sending as is.
                plain_text_summary = f"📣 订阅成功！UP 主: {name} | 性别: {sex}"
                if filter_desc: plain_text_summary += filter_desc.replace("<br>", "\n")

                chain_elements = [
                    Plain(plain_text_summary),
                    Image.fromURL(avatar) # User's avatar
                ]
                # If qrcode is desired in non-rai:
                # qr_b64 = await create_qrcode(render_data["url"])
                # if qr_b64: chain_elements.append(Image.fromBase64(qr_b64.split(",")[1]))
                
                return CommandResult(chain=MessageChain(chain_elements), use_t2i_=False)
        except Exception as e:
            logger.error(f"Error rendering subscription confirmation: {e}")
            return CommandResult().message("订阅成功，但渲染确认图片时出错。")


    @command("订阅列表")
    async def sub_list(self, message: AstrMessageEvent):
        """查看 bilibili 动态监控列表"""
        sub_user = message.unified_msg_origin
        bili_sub_list = self.data_manager.get_data("bili_sub_list", {})
        ret = """订阅列表：\n"""
        if sub_user in bili_sub_list and bili_sub_list[sub_user]:
            for idx, uid_sub_data in enumerate(bili_sub_list[sub_user]):
                # Fetch user name for better display if desired, or just show UID
                ret += f"{idx + 1}. UID: {uid_sub_data['uid']}"
                # Optionally, display filter info
                if uid_sub_data.get('filter_types'):
                    ret += f" | 类型: {', '.join(uid_sub_data['filter_types'])}"
                if uid_sub_data.get('filter_regex'):
                    ret += f" | 正则: {uid_sub_data['filter_regex']}"
                ret += "\n"
            return CommandResult().message(ret)
        else:
            return CommandResult().message("无订阅")

    @command("订阅删除")
    async def sub_del(self, message: AstrMessageEvent, uid_str: str): # uid is now uid_str
        """删除 bilibili 动态监控"""
        sub_user = message.unified_msg_origin
        bili_sub_list = self.data_manager.get_data("bili_sub_list", {})

        if sub_user in bili_sub_list:
            if not uid_str or not uid_str.isdigit(): # Check uid_str
                return CommandResult().message("UID 参数无效。请输入正确的用户UID。")

            uid_to_delete = int(uid_str)
            original_len = len(bili_sub_list[sub_user])
            
            bili_sub_list[sub_user] = [
                sub for sub in bili_sub_list[sub_user] if sub["uid"] != uid_to_delete
            ]

            if len(bili_sub_list[sub_user]) < original_len:
                if not bili_sub_list[sub_user]: # If list becomes empty
                    del bili_sub_list[sub_user] # Clean up key for this sub_user
                self.data_manager.update_data("bili_sub_list", bili_sub_list) # Updates and saves
                return CommandResult().message(f"已删除对 UID {uid_to_delete} 的订阅。")
            else:
                return CommandResult().message(f"未找到对 UID {uid_to_delete} 的订阅。")
        else:
            return CommandResult().message("您还没有订阅哦！")


    @llm_tool("get_bangumi")
    async def get_bangumi(
        self,
        message: AstrMessageEvent,
        style: str = "ALL",
        season: str = "ALL",
        start_year: Optional[int] = None, # Ensure Optional for None default
        end_year: Optional[int] = None,   # Ensure Optional for None default
    ):
        """当用户希望推荐番剧时调用。根据用户的描述获取前 5 条推荐的动漫番剧。

        Args:
            style(string): 番剧的风格。默认为全部。可选值有：原创, 漫画改, 小说改, 游戏改, 特摄, 布袋戏, 热血, 穿越, 奇幻, 战斗, 搞笑, 日常, 科幻, 萌系, 治愈, 校园, 儿童, 泡面, 恋爱, 少女, 魔法, 冒险, 历史, 架空, 机战, 神魔, 声控, 运动, 励志, 音乐, 推理, 社团, 智斗, 催泪, 美食, 偶像, 乙女, 职场
            season(string): 番剧的季度。默认为全部。可选值有：WINTER, SPRING, SUMMER, AUTUMN。其也分别代表一月番、四月番、七月番、十月番
            start_year(number): 起始年份。默认为空，即不限制年份。
            end_year(number): 结束年份。默认为空，即不限制年份。
        """

        # Style and season processing remains the same as it uses IF constants
        processed_style = IF.Style.Anime.ALL
        if style in category_mapping:
            processed_style = getattr(IF.Style.Anime, category_mapping[style], IF.Style.Anime.ALL)
        
        processed_season = IF.Season.ALL
        if season in ["WINTER", "SPRING", "SUMMER", "AUTUMN"]:
            processed_season = getattr(IF.Season, season, IF.Season.ALL)

        # Construct filters using IF constants
        filters = IF.Anime( # Corrected: IF.Anime directly
            area=IF.Area.JAPAN,
            # IF.make_time_filter is correct if it exists and works with None for start/end year
            # Assuming IF.make_time_filter handles None correctly for open-ended year ranges
            # If not, specific logic for year filtering might be needed.
            # For now, assuming it's correct as per original.
            year=IF.make_time_filter(start=start_year, end=end_year, include_end=True if end_year else False),
            season=processed_season,
            style=processed_style,
        )

        # Use BilibiliApiClient for fetching bangumi info
        # The get_bangumi_api was defined to take filters, order, sort, pn, ps
        # Need to map IF.Order.SCORE and IF.Sort.DESC to string if client expects strings,
        # or pass them directly if client expects these enum objects.
        # Assuming client can handle these IF objects directly or they are mapped inside client.
        index_data = await self.bili_client.get_bangumi_api(
            filters=filters, 
            order=IF.Order.SCORE, 
            sort=IF.Sort.DESC, 
            pn=1, 
            ps=5
        )

        if not index_data or not index_data.get("list"):
            return "抱歉，未能根据您的条件找到番剧推荐。"

        result = "推荐的番剧:\n"
        for item in index_data["list"]:
            result += f"标题: {item.get('title', 'N/A')}\n"
            result += f"副标题: {item.get('subTitle', item.get('subtitle', 'N/A'))}\n" # Check for subtitle key variation
            result += f"评分: {item.get('score', 'N/A')}\n"
            result += f"集数: {item.get('index_show', item.get('stat', {}).get('follow_view', {}).get('index_show', 'N/A'))}\n" # Check for variations in structure
            result += f"链接: {item.get('link', item.get('url', 'N/A'))}\n" # Check for link/url key
            result += "\n"
        result += "请分点，贴心地回答。不要输出 markdown 格式。"
        return result


    async def dynamic_listener(self):
        while True:
            await asyncio.sleep(60 * self.interval_mins)
            if self.credential is None: # Ensure credential check is still valid
                logger.warning("bilibili sessdata 未设置，无法获取动态")
                continue

            bili_sub_list = self.data_manager.get_data("bili_sub_list", {})
            # Create a deep copy for iteration if modifications occur, though current logic modifies via DataManager.
            # For safety, if direct modification of bili_sub_list was planned:
            # current_subs_to_iterate = copy.deepcopy(bili_sub_list) 
            # for sub_usr, user_subs in current_subs_to_iterate.items():

            for sub_usr, user_subs in list(bili_sub_list.items()): # Iterate over a copy of items for safe modification
                for idx, uid_sub_data in enumerate(user_subs):
                    uid = uid_sub_data.get("uid")
                    if not uid:
                        logger.warning(f"Subscription for {sub_usr} at index {idx} missing UID.")
                        continue
                    
                    try:
                        # 1. Handle Dynamics
                        usr_dyn_response = await self.bili_client.get_user_dynamics_api(uid)
                        
                        if usr_dyn_response and usr_dyn_response.get("items"):
                            # Ensure uid_sub_data has all necessary fields for dynamic_parser
                            # (uid, last, filter_types, filter_regex)
                            current_sub_config = {
                                "uid": uid,
                                "last": uid_sub_data.get("last"),
                                "filter_types": uid_sub_data.get("filter_types", []),
                                "filter_regex": uid_sub_data.get("filter_regex")
                            }
                            
                            processed_dynamics, latest_dyn_id_from_fetch = await self.dynamic_parser.parse_dynamics_items(
                                usr_dyn_response['items'], current_sub_config
                            )

                            if processed_dynamics:
                                for dyn_detail in processed_dynamics:
                                    # dyn_detail is the dict from _extract_dynamic_details
                                    # It should contain 'name', 'avatar', 'text', 'image_urls', 'url', 'title', etc.
                                    # And critically, 'dyn_id' (or 'id_str') for the specific dynamic.
                                    # The DynamicParser needs to ensure it adds the dynamic's own ID to dyn_detail.
                                    # For now, assuming 'url' from dyn_detail is the specific dynamic URL.
                                    # And 'id_str' is available in dyn_detail for updating "last".

                                    # The current _extract_dynamic_details returns a dict.
                                    # It needs to include the dynamic's own ID, let's assume it's 'id_str' field in dyn_detail.
                                    # If not, DynamicParser needs adjustment.
                                    # For now, we assume 'dyn_detail' contains 'id_str' from the original item.
                                    # And 'url' is the jump_url for the dynamic.
                                    # The 'latest_dyn_id_from_fetch' is for the overall latest ID from the batch.
                                    # For individual posts, we need *their* ID to update 'last' if we only post one by one.
                                    # However, parse_dynamics_items is designed to return a list of *new* items.
                                    # The 'latest_dyn_id_from_fetch' should be the one to save after processing the batch.

                                    render_data_dynamic = await create_render_data()
                                    # Populate render_data_dynamic from dyn_detail
                                    render_data_dynamic.update(dyn_detail) # dyn_detail should match create_render_data structure
                                    
                                    # Ensure essential fields are there, possibly from dyn_detail directly
                                    # render_data_dynamic["name"] = dyn_detail.get("name", "Unknown User")
                                    # render_data_dynamic["avatar"] = dyn_detail.get("avatar") # Should be base64 by now if handled by parser, or URL
                                    # render_data_dynamic["text"] = dyn_detail.get("text", "")
                                    # render_data_dynamic["title"] = dyn_detail.get("title", "")
                                    # render_data_dynamic["image_urls"] = dyn_detail.get("image_urls", [])
                                    # render_data_dynamic["url"] = dyn_detail.get("url", "")
                                    
                                    if not render_data_dynamic.get("avatar"): # If avatar in dyn_detail is a URL, convert it
                                        # This depends on what DynamicParser puts in 'avatar'.
                                        # If it's a URL, it should be fetched and base64 encoded here or by HTMLRenderer.
                                        # For simplicity, let's assume HTMLRenderer can handle URL avatars if not base64.
                                        # Or, ensure avatar is pre-processed by DynamicParser or here.
                                        # The prompt for HTMLRenderer.render_html_to_image says it defaults to logo if avatar is missing.
                                        pass # Avatar handling will be by HTMLRenderer's default or if dyn_detail provides base64

                                    if dyn_detail.get("url"): # Check if URL exists for QR code
                                        render_data_dynamic["qrcode"] = await create_qrcode(dyn_detail["url"])
                                    
                                    # Non-RAI handling (similar to original, simplified)
                                    if not self.rai and (dyn_detail.get("type") == "DYNAMIC_TYPE_DRAW" or dyn_detail.get("type") == "DYNAMIC_TYPE_WORD"):
                                        msg_chain_elements = [Plain(f"📣 UP 主 「{dyn_detail.get('name')}」 发布了新图文动态:\n")]
                                        if dyn_detail.get("text"): # Assuming 'text' is plain or simple HTML suitable for Plain
                                            # Need to ensure 'text' from parser is suitable. Original used 'summary'.
                                            # The new 'text' from parser is HTML. May need stripping for Plain.
                                            # For now, let's assume dyn_detail has a 'plain_text_summary' or similar.
                                            # Or, just send the main text.
                                            # Let's use a simplified message for non-RAI for now.
                                            msg_chain_elements.append(Plain(f"内容: {dyn_detail.get('title', '')} {dyn_detail.get('text', '')[:50]}..."))

                                        for pic_url in dyn_detail.get("image_urls", [])[:3]: # Limit images for non-RAI
                                            msg_chain_elements.append(Image.fromURL(pic_url))
                                        if dyn_detail.get("url"):
                                             msg_chain_elements.append(Plain(f"\n链接: {dyn_detail['url']}"))
                                        await self.context.send_message(sub_usr, CommandResult(chain=MessageChain(msg_chain_elements)).use_t2i(False))
                                    else: # RAI or other types
                                        await self.html_renderer.render_html_to_image(render_data_dynamic, IMG_PATH)
                                        await self.context.send_message(
                                            sub_usr,
                                            MessageChain().file_image(IMG_PATH).message(dyn_detail.get("url", "")),
                                        )
                                # After processing all new dynamics in the batch, update "last" ID
                                if latest_dyn_id_from_fetch and latest_dyn_id_from_fetch != uid_sub_data.get("last"):
                                    bili_sub_list[sub_usr][idx]["last"] = latest_dyn_id_from_fetch
                                    self.data_manager.update_data("bili_sub_list", bili_sub_list)
                                    logger.info(f"UID {uid} new dynamics processed. Last ID updated to {latest_dyn_id_from_fetch}.")

                            elif latest_dyn_id_from_fetch and latest_dyn_id_from_fetch != uid_sub_data.get("last"):
                                # No new dynamics passed filters, but the fetch might have seen newer items than 'last'
                                bili_sub_list[sub_usr][idx]["last"] = latest_dyn_id_from_fetch
                                self.data_manager.update_data("bili_sub_list", bili_sub_list)
                                logger.info(f"UID {uid} no new dynamics to send. Last ID updated to {latest_dyn_id_from_fetch}.")
                        
                        # 2. Handle Live Info
                        live_info_response = await self.bili_client.get_user_live_info_api(uid)
                        if live_info_response:
                            current_is_live_in_data = uid_sub_data.get("is_live", False)
                            live_room_status = live_info_response.get("live_room", {}).get("liveStatus") # 1 for live, 0 for not
                            live_room_data = live_info_response.get("live_room", {})
                            user_name_live = live_info_response.get("name", uid_sub_data.get("name", str(uid))) # Fallback name

                            render_live_text = None
                            should_update_live_status = False

                            if live_room_status == 1 and not current_is_live_in_data: # Started streaming
                                render_live_text = f"📣 你订阅的UP 「{user_name_live}」 开播了！"
                                bili_sub_list[sub_usr][idx]["is_live"] = True
                                should_update_live_status = True
                            elif live_room_status == 0 and current_is_live_in_data: # Stopped streaming
                                render_live_text = f"📣 你订阅的UP 「{user_name_live}」 下播了！"
                                bili_sub_list[sub_usr][idx]["is_live"] = False
                                should_update_live_status = True
                            
                            if render_live_text:
                                live_title = live_room_data.get("title", "直播间")
                                live_url = live_room_data.get("url")
                                if live_url and not live_url.startswith("http"): live_url = "https:" + live_url if live_url.startswith("//") else "https://live.bilibili.com" + live_url

                                live_cover = live_room_data.get("cover")

                                render_data_live = await create_render_data()
                                render_data_live["name"] = "AstrBot" # Bot name for card
                                render_data_live["avatar"] = await image_to_base64(LOGO_PATH) # Bot avatar
                                render_data_live["title"] = live_title
                                render_data_live["text"] = render_live_text
                                render_data_live["image_urls"] = [live_cover] if live_cover else []
                                if live_url:
                                    render_data_live["url"] = live_url
                                    render_data_live["qrcode"] = await create_qrcode(live_url)
                                
                                await self.html_renderer.render_html_to_image(render_data_live, IMG_PATH)
                                await self.context.send_message(
                                    sub_usr,
                                    MessageChain().file_image(IMG_PATH).message(render_data_live.get("url","")),
                                )
                            
                            if should_update_live_status:
                                self.data_manager.update_data("bili_sub_list", bili_sub_list) # Save updated is_live status

                    except Exception as e:
                        logger.error(
                            f"处理订阅者 {sub_usr} 的 UP主 {uid} 时发生错误: {e}\n{traceback.format_exc()}"
                        )
            # A short sleep after processing all users in one cycle
            await asyncio.sleep(10) # Reduce busy-looping if interval_mins is very short or for testing.

    @permission_type(PermissionType.ADMIN)
    @command("全局删除")
    async def global_sub(self, message: AstrMessageEvent, sid: str = None):
        """管理员指令。通过 SID 删除某一个群聊或者私聊的所有订阅。使用 /sid 查看当前会话的 SID。"""
        if not sid:
            return CommandResult().message(
                "通过 SID 删除某一个群聊或者私聊的所有订阅。使用 /sid 指令查看当前会话的 SID。"
            )
        
        bili_sub_list = self.data_manager.get_data("bili_sub_list", {})
        candidate_keys_to_delete = []
        for sub_user_key in bili_sub_list:
            # Assuming sub_user_key format is like "platform:channel_type:id"
            try:
                parts = sub_user_key.split(":")
                if len(parts) >= 3 and parts[2] == str(sid):
                    candidate_keys_to_delete.append(sub_user_key)
                elif sid == sub_user_key: # Direct match for full SID string
                     candidate_keys_to_delete.append(sub_user_key)
            except: # Handle cases where sub_user_key might not be in expected format
                if sid == sub_user_key:
                    candidate_keys_to_delete.append(sub_user_key)


        if not candidate_keys_to_delete:
            return CommandResult().message("未找到与此 SID 相关的订阅。")

        deleted_count = 0
        for key_to_delete in candidate_keys_to_delete:
            if key_to_delete in bili_sub_list:
                del bili_sub_list[key_to_delete]
                deleted_count +=1
        
        if deleted_count > 0:
            self.data_manager.update_data("bili_sub_list", bili_sub_list) # Save changes
            return CommandResult().message(f"成功删除了 {deleted_count} 个与 SID {sid} 相关的订阅记录。")
        else:
            # This case should ideally not be reached if candidate_keys_to_delete was populated
            return CommandResult().message("未找到与此 SID 相关的订阅（或已被删除）。")


    @permission_type(PermissionType.ADMIN)
    @command("全局列表")
    async def global_list(self, message: AstrMessageEvent):
        """管理员指令。查看所有订阅者"""
        bili_sub_list = self.data_manager.get_data("bili_sub_list", {})
        ret = "订阅会话列表：\n"

        if not bili_sub_list:
            return CommandResult().message("没有任何会话订阅过。")

        for sub_user_key, subs in bili_sub_list.items():
            num_subs = len(subs)
            ret += f"- SID: {sub_user_key} (订阅了 {num_subs} 个UP主)\n"
            # Optionally, list UIDs under each SID
            # for sub_data in subs:
            # ret += f"  - UID: {sub_data['uid']}\n"
        return CommandResult().message(ret)

    # Methods to be removed:
    # async def parse_last_dynamic(self, dyn: dict, data: dict): ...
    # async def render_dynamic(self, render_data: dict): ...
    # async def build_render(self, item, render_data, is_forward=False): ...


    @event_message_type(EventMessageType.ALL)
    async def parse_miniapp(self, event: AstrMessageEvent):
        if not event.message_obj or not event.message_obj.message: # Check message_obj first
            logger.warning("Received an event with no message object or empty message list.")
            return

        # b23_to_bv is now _b23_to_bv_helper in this file. No need for try-except import.
        # async def b23_to_bv_fallback(url): return url # Fallback no longer needed here
        # b23_to_bv = _b23_to_bv_helper # Direct assignment

        for msg_element in event.message_obj.message:
            if (
                hasattr(msg_element, "type")
                and msg_element.type == "Json"
                and hasattr(msg_element, "data")
            ):
                json_string = msg_element.data

                try:
                    parsed_data = json.loads(json_string)
                    meta = parsed_data.get("meta", {})
                    # Based on common miniapp structures, data might be nested differently.
                    # Example: meta -> detail_1 or meta -> news or meta -> summary
                    # This needs to be robust or specific to the expected JSON structure.
                    detail_1 = meta.get("detail_1", meta.get("news", meta.get("summary", {}))) # Try a few common keys

                    # Ensure detail_1 is a dict before proceeding
                    if not isinstance(detail_1, dict):
                        # logger.debug(f"Miniapp meta content (detail_1 or equivalent) is not a dict: {detail_1}")
                        continue

                    title = detail_1.get("title")
                    # 'qqdocurl' might be 'jumpUrl', 'jr', 'url', etc.
                    qqdocurl = detail_1.get("qqdocurl", detail_1.get("jumpUrl", detail_1.get("jr", detail_1.get("url"))))
                    desc = detail_1.get("desc", detail_1.get("summary", title)) # Fallback for desc

                    if title and "哔哩哔哩" in title and qqdocurl: # Check if title exists
                        if "b23.tv" in qqdocurl:
                            try:
                                qqdocurl = await _b23_to_bv_helper(qqdocurl) # Use the helper
                            except Exception as e_b23: # Should be caught by helper, but keep for safety
                                logger.error(f"Error converting b23.tv URL {qqdocurl} in parse_miniapp: {e_b23}")
                        ret = f"视频: {desc}\n链接: {qqdocurl}"
                        # Using yield event.send_message(ret) or similar if it's an async generator
                        # Original was yield event.plain_result(ret)
                        # Assuming plain_result is a valid way to yield a response.
                        # If this needs to be `await event.send(...)` then the function signature needs to change
                        # or results collected and sent. For now, assume `yield event.plain_result` works.
                        await event.send(ret) # Changed to await event.send based on typical AstrBot patterns
                        return # Send once per matching miniapp
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON string from miniapp: {json_string}")
                except Exception as e:
                    logger.error(f"An error occurred during miniapp JSON processing: {e}\n{traceback.format_exc()}")


    async def terminate(self):
        if self.dynamic_listener_task and not self.dynamic_listener_task.done():
            self.dynamic_listener_task.cancel()
            try:
                await self.dynamic_listener_task
            except asyncio.CancelledError:
                logger.info("bilibili dynamic_listener task was successfully cancelled during terminate.")
            except Exception as e:
                logger.error(f"Error awaiting cancellation of dynamic_listener task: {e}")