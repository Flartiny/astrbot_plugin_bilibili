import re
import logging
from typing import List, Dict, Any, Tuple, Optional

# Moved from utils.py
async def parse_rich_text(summary: Dict[str, Any], topic: Optional[Dict[str, Any]]) -> str:
    text = "<br>".join(filter(None, summary.get("text", "").split("\n")))
    # 真正的话题
    if topic:
        topic_link = f"<a href='{topic.get('jump_url', '')}'># {topic.get('name', '')}</a>"
        text = f"# {topic_link}<br>" + text
    # 获取富文本节点
    rich_text_nodes = summary.get("rich_text_nodes", [])
    for node in rich_text_nodes:
        # 表情包
        if node.get("type") == "RICH_TEXT_NODE_TYPE_EMOJI":
            emoji_info = node.get("emoji", {})
            placeholder = emoji_info.get("text", "")  # 例如 "[脱单doge]"
            img_tag = f"<img src='{emoji_info.get('icon_url', '')}'>"
            # 替换文本中的占位符
            if placeholder:
                text = text.replace(placeholder, img_tag)
        # 话题形如"#一个话题#"，实际是跳转搜索
        elif node.get("type") == "RICH_TEXT_NODE_TYPE_TOPIC":
            topic_info_text = node.get("text", "")
            topic_url = node.get("jump_url", "")
            if topic_info_text and topic_url:
                topic_tag = f"<a href='https:{topic_url}'>{topic_info_text}</a>"
                # 替换文本中的占位符
                text = text.replace(topic_info_text, topic_tag)
    return text


class DynamicParser:
    def __init__(self):
        pass

    async def _extract_dynamic_details(self, item: Dict[str, Any], is_forward: bool = False) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "name": "",
            "avatar": "",
            "pendant": "",
            "text": "",
            "image_urls": [],
            "url": "",
            "title": "",
            "type": item.get("type", "UNKNOWN_TYPE"), # Store the original type
            "bvid": None, # For video dynamics
            "orig_type": None, # For forwarded dynamics
            "orig_name": None, # For forwarded dynamics
            "orig_text": None, # For forwarded dynamics
            "orig_image_urls": [], # For forwarded dynamics
            "orig_url": None, # For forwarded dynamics
            "orig_title": None, # For forwarded dynamics
        }

        module_author = item.get("modules", {}).get("module_author", {})
        details["name"] = module_author.get("name", "未知用户")
        details["avatar"] = module_author.get("face", "")
        details["pendant"] = module_author.get("pendant", {}).get("image", "")
        details["url"] = f"https://t.bilibili.com/{item.get('id_str', '')}"

        module_dynamic = item.get("modules", {}).get("module_dynamic", {})
        
        if is_forward:
            # This is the text of the forwarder
            desc = module_dynamic.get("desc")
            if desc:
                details["text"] = await parse_rich_text(desc, module_dynamic.get("topic"))
            else:
                details["text"] = "转发动态" # Default text for forward

            # Now process the original item
            orig_item_json = item.get("orig")
            if not orig_item_json:
                logging.warning(f"Forwarded dynamic {item.get('id_str')} has no original item content.")
                return details # Or handle as an error/skip

            orig_module_author = orig_item_json.get("modules", {}).get("module_author", {})
            details["orig_name"] = orig_module_author.get("name", "未知用户")
            details["orig_type"] = orig_item_json.get("type", "UNKNOWN_TYPE")
            details["orig_url"] = f"https://t.bilibili.com/{orig_item_json.get('id_str', '')}"
            
            orig_module_dynamic = orig_item_json.get("modules", {}).get("module_dynamic", {})
            orig_desc = orig_module_dynamic.get("desc")
            orig_topic = orig_module_dynamic.get("topic")

            if orig_desc:
                details["orig_text"] = await parse_rich_text(orig_desc, orig_topic)
            
            if orig_item_json["type"] == "DYNAMIC_TYPE_AV":
                major_archive = orig_module_dynamic.get("major", {}).get("archive", {})
                details["orig_title"] = major_archive.get("title", "视频已失效")
                details["orig_image_urls"] = [major_archive.get("cover")] if major_archive.get("cover") else []
                details["bvid"] = major_archive.get("bvid") # bvid of the original video
                if not details["orig_url"] and major_archive.get("jump_url"): # Prefer dynamic link if available
                    details["orig_url"] = "https:" + major_archive.get("jump_url") if major_archive.get("jump_url").startswith("//") else major_archive.get("jump_url")

            elif orig_item_json["type"] == "DYNAMIC_TYPE_DRAW":
                major_draw = orig_module_dynamic.get("major", {}).get("draw", {})
                details["orig_image_urls"] = [d.get("src", "") for d in major_draw.get("items", []) if d.get("src")]
            
            elif orig_item_json["type"] == "DYNAMIC_TYPE_ARTICLE":
                major_article = orig_module_dynamic.get("major", {}).get("article", {})
                details["orig_title"] = major_article.get("title", "专栏已失效")
                details["orig_image_urls"] = [cover for cover in major_article.get("covers", []) if cover]
                if not details["orig_url"] and major_article.get("jump_url"):
                     details["orig_url"] = "https:" + major_article.get("jump_url") if major_article.get("jump_url").startswith("//") else major_article.get("jump_url")

            # Other original types can be added here (WORD, LIVE_RCMD, etc.)

        else: # Not a forward
            desc = module_dynamic.get("desc")
            if desc:
                details["text"] = await parse_rich_text(desc, module_dynamic.get("topic"))

            if item["type"] == "DYNAMIC_TYPE_AV":
                major_archive = module_dynamic.get("major", {}).get("archive", {})
                details["title"] = major_archive.get("title", "视频已失效")
                details["image_urls"] = [major_archive.get("cover")] if major_archive.get("cover") else []
                details["bvid"] = major_archive.get("bvid")
                if not details["url"] and major_archive.get("jump_url"): # Prefer dynamic link if available
                    details["url"] = "https:" + major_archive.get("jump_url") if major_archive.get("jump_url").startswith("//") else major_archive.get("jump_url")
            
            elif item["type"] == "DYNAMIC_TYPE_DRAW":
                major_draw = module_dynamic.get("major", {}).get("draw", {})
                details["image_urls"] = [d.get("src", "") for d in major_draw.get("items", []) if d.get("src")]
            
            elif item["type"] == "DYNAMIC_TYPE_WORD":
                # Text is already handled by desc
                pass
            
            elif item["type"] == "DYNAMIC_TYPE_ARTICLE":
                major_article = module_dynamic.get("major", {}).get("article", {})
                details["title"] = major_article.get("title", "专栏已失效")
                details["image_urls"] = [cover for cover in major_article.get("covers", []) if cover]
                if not details["url"] and major_article.get("jump_url"):
                     details["url"] = "https:" + major_article.get("jump_url") if major_article.get("jump_url").startswith("//") else major_article.get("jump_url")
            
            # DYNAMIC_TYPE_LIVE_RCMD (直播推荐) / DYNAMIC_TYPE_LIVE (开播)
            elif item["type"] == "DYNAMIC_TYPE_LIVE_RCMD" or item["type"] == "DYNAMIC_TYPE_LIVE":
                major_live = module_dynamic.get("major", {}).get("live_rcmd", {}) # V1 API
                if not major_live and item["type"] == "DYNAMIC_TYPE_LIVE": # V2 API for DYNAMIC_TYPE_LIVE
                    major_live = module_dynamic.get("major", {}).get("live", {})

                if major_live: # Check if major_live has content
                    live_play_info = major_live.get("live_play_info", {})
                    details["title"] = live_play_info.get("title", "直播间")
                    details["image_urls"] = [live_play_info.get("cover", "")] if live_play_info.get("cover") else []
                    details["url"] = live_play_info.get("link", "") 
                    if details["url"].startswith("//"): # Ensure URL has scheme
                        details["url"] = "https:" + details["url"]
                else: # Fallback or if live info is structured differently
                    live_card_small = item.get("modules",{}).get("module_dynamic",{}).get("additional",{}).get("live_card_small",{})
                    if live_card_small:
                        details["title"] = live_card_small.get("title", "直播中")
                        details["image_urls"] = [live_card_small.get("cover")] if live_card_small.get("cover") else []
                        details["url"] = live_card_small.get("jump_url", "")
                        if details["url"].startswith("//"):
                            details["url"] = "https:" + details["url"]
                    else:
                         logging.warning(f"Could not extract live info for dynamic {item.get('id_str')}")
                         details["title"] = "直播动态" # Default title

        # Clean up empty strings from image_urls
        details["image_urls"] = [url for url in details["image_urls"] if url]
        details["orig_image_urls"] = [url for url in details["orig_image_urls"] if url]
        
        return details

    async def parse_dynamics_items(self, dyn_items: List[Dict[str, Any]], sub_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        processed_dynamics = []
        latest_dyn_id = sub_data.get("last") 
        new_latest_dyn_id = latest_dyn_id # Will be updated if newer items are processed

        uid = sub_data.get("uid", "Unknown UID")
        filter_types = sub_data.get("filter_types", [])
        filter_regex = sub_data.get("filter_regex")
        compiled_regex = None
        if filter_regex:
            try:
                compiled_regex = re.compile(filter_regex)
            except re.error as e:
                logging.error(f"Invalid regex for UID {uid}: {filter_regex} - {e}")
                compiled_regex = None # Disable regex filtering if compilation fails
        
        dyn_items.sort(key=lambda x: int(x.get("modules", {}).get("module_author", {}).get("pub_ts", 0)), reverse=True)


        for item in dyn_items:
            item_id_str = item.get("id_str")
            if not item_id_str:
                logging.warning(f"Dynamic item for UID {uid} missing id_str. Skipping.")
                continue

            # Update new_latest_dyn_id with the first item's ID if it's the newest overall
            if new_latest_dyn_id is None or int(item_id_str) > int(new_latest_dyn_id):
                 new_latest_dyn_id = item_id_str
            
            # 1. Filter by last processed ID
            if latest_dyn_id and int(item_id_str) <= int(latest_dyn_id):
                logging.info(f"UID {uid} - Dynamic {item_id_str} is old or already processed. Skipping.")
                continue

            item_type = item.get("type")
            module_author = item.get("modules", {}).get("module_author", {})
            
            # 2. Filter by "置顶" (Pinned)
            if module_author.get("label") == "置顶":
                logging.info(f"UID {uid} - Dynamic {item_id_str} is pinned. Skipping.")
                continue

            # 3. Filter by dynamic type (filter_types)
            if filter_types and item_type not in filter_types:
                logging.info(f"UID {uid} - Dynamic {item_id_str} (type: {item_type}) filtered out by type. Skipping.")
                continue
            
            logging.info(f"UID {uid} - Processing dynamic: {item_id_str}, Type: {item_type}")

            details = {}
            if item_type == "DYNAMIC_TYPE_FORWARD":
                details = await self._extract_dynamic_details(item, is_forward=True)
                # Regex check for forwarded dynamics (check both forwarder's text and original text)
                text_to_check = (details.get("text", "") or "") + " " + (details.get("orig_text", "") or "")
                title_to_check = details.get("orig_title","") or ""
            else:
                details = await self._extract_dynamic_details(item, is_forward=False)
                text_to_check = details.get("text", "") or ""
                title_to_check = details.get("title","") or ""

            # 4. Filter by regex
            if compiled_regex:
                content_to_match = f"{text_to_check} {title_to_check}"
                if not compiled_regex.search(content_to_match):
                    logging.info(f"UID {uid} - Dynamic {item_id_str} did not match regex. Skipping. Content: '{content_to_match[:100]}...'")
                    continue
            
            # If all filters passed, add to list
            processed_dynamics.append(details)
            logging.info(f"UID {uid} - Dynamic {item_id_str} passed all filters. Added for processing.")

        # Sort by pub_ts before returning, oldest new first for sequential processing by caller
        # The pub_ts is part of the original item structure, not directly in 'details' yet.
        # For now, we rely on the initial sort and the fact that we add them in order.
        # If strict chronological order of *pushed* items is critical, we'd need to add pub_ts to 'details'.
        
        # The list is already effectively sorted by pub_ts due to the initial sort and processing order.
        # If we filtered out older items, the new_latest_dyn_id should reflect the newest among all items fetched,
        # not just the ones that passed filters.
        
        # Find the actual latest ID from all items fetched, regardless of filtering
        true_latest_id_from_fetch = latest_dyn_id
        if dyn_items:
            current_max_id_num = 0
            if true_latest_id_from_fetch:
                current_max_id_num = int(true_latest_id_from_fetch)

            for item_to_check_id in dyn_items:
                id_num = int(item_to_check_id.get("id_str", "0"))
                if id_num > current_max_id_num:
                    current_max_id_num = id_num
            if current_max_id_num > 0 and (true_latest_id_from_fetch is None or current_max_id_num > int(true_latest_id_from_fetch)):
                 true_latest_id_from_fetch = str(current_max_id_num)


        if not processed_dynamics:
            logging.info(f"UID {uid} - No new dynamics after filtering. Latest ID from fetch: {true_latest_id_from_fetch}")
            return [], true_latest_id_from_fetch # Return the latest ID from the fetched items

        logging.info(f"UID {uid} - Found {len(processed_dynamics)} new dynamics. New latest ID to store: {true_latest_id_from_fetch}")
        return processed_dynamics, true_latest_id_from_fetch
