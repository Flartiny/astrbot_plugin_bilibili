from astrbot.api.all import *
import PIL
import aiohttp
import qrcode
import io
import base64
import os
from urllib.parse import urlparse


async def create_render_data() -> dict:
    return {
        "name": "",
        "avatar": "",
        "pendant": "",
        "text": "",
        "image_urls": [],
        "qrcode": "",
        "url": "",
    }


async def image_to_base64(image_source, mime_type: str = "image/png") -> str:
    """
    将图片对象或文件路径转为Base64 Data URI
    :param image_source: PIL Image对象 或 图片文件路径
    :param mime_type: 图片MIME类型，默认image/png
    :return: Base64 Data URI字符串
    """
    buffer = io.BytesIO()

    # 处理PIL Image对象
    if hasattr(image_source, "save"):
        image_source.save(buffer, format=mime_type.split("/")[-1])
    # 处理文件路径
    elif isinstance(image_source, str):
        with open(image_source, "rb") as f:
            buffer.write(f.read())
    else:
        raise ValueError("Unsupported image source type")

    base64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:{mime_type};base64,{base64_str}"


async def create_qrcode(url):
    if not is_valid_url(url):
        return ""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="#EC88EC", back_color="#F2F6FF")
    url = await image_to_base64(qr_image)
    return url


async def get_and_crop_image(src, output_path, width=640):
    if src.startswith(("http://", "https://")):
        async with aiohttp.ClientSession() as session:
            async with session.get(src, timeout=10) as response:
                if response.status != 200:
                    return
                data = await response.read()
                image = PIL.Image.open(io.BytesIO(data))
    else:
        if not os.path.exists(src):
            return
        image = PIL.Image.open(src)
    w, h = image.size
    cropped = image.crop((0, 0, min(width, w), h))
    cropped.save(output_path)


def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return all([parsed.scheme, parsed.netloc])
    except ValueError:
        return False


async def parse_rich_text(summary, topic):
    text = summary["text"].replace("\n", "<br>")
    if topic:
        topic_link = f"<a href='{topic['jump_url']}'>{topic['name']}</a>"
        text = f"# {topic_link}<br>" + text
    # 获取富文本节点
    rich_text_nodes = summary["rich_text_nodes"]

    for node in rich_text_nodes:
        if node["type"] == "RICH_TEXT_NODE_TYPE_EMOJI":
            emoji_info = node["emoji"]
            placeholder = emoji_info["text"]  # 例如 "[脱单doge]"
            img_tag = f"<img src='{emoji_info['icon_url']}' alt='{placeholder}'>"

            # 替换文本中的占位符
            text = text.replace(placeholder, img_tag)

    return text
