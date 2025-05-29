import os
import asyncio
import logging
import io
import base64
from typing import Callable, Dict, List, Any, Union

import PIL.Image
import aiohttp
import qrcode # Added for create_qrcode
from urllib.parse import urlparse # For is_valid_url, used by create_qrcode

# Module level or class constants
MAX_ATTEMPTS = 3
RETRY_DELAY = 2

# Moved from utils.py
async def create_render_data() -> Dict[str, Any]:
    return {
        "name": "",  # 图中header处用户名
        "avatar": "",  # 头像url (will be base64)
        "pendant": "",  # 头像框url
        "text": "",  # 正文
        "image_urls": [],  # 正文图片url列表 (will be base64 if local, or kept as URL for template)
        "qrcode": "",  # qrcode url (base64)
        "url": "",  # 用于渲染qrcode，也用于构成massagechain
        "title": "",  # 标题(视频标题、动态标题)
    }

# Moved from utils.py
async def image_to_base64(image_source: Union[PIL.Image.Image, str], mime_type: str = "image/png") -> str:
    """
    将图片对象或文件路径转为Base64 Data URI
    :param image_source: PIL Image对象 或 图片文件路径
    :param mime_type: 图片MIME类型，默认image/png
    :return: Base64 Data URI字符串
    """
    buffer = io.BytesIO()

    if isinstance(image_source, PIL.Image.Image): # Check if it's already a PIL Image object
        image_source.save(buffer, format=mime_type.split("/")[-1])
    elif isinstance(image_source, str): # Assume it's a file path
        if not os.path.exists(image_source):
            logging.error(f"Image file not found at path: {image_source}")
            return "" # Return empty string or raise error
        try:
            with open(image_source, "rb") as f:
                buffer.write(f.read())
        except IOError as e:
            logging.error(f"Error reading image file {image_source}: {e}")
            return ""
    else:
        logging.error(f"Unsupported image source type: {type(image_source)}")
        raise ValueError("Unsupported image source type")

    base64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:{mime_type};base64,{base64_str}"

# Helper for create_qrcode, moved from utils.py
def is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return all([parsed.scheme, parsed.netloc])
    except ValueError:
        return False

# Moved from utils.py
async def create_qrcode(url: str) -> str:
    if not is_valid_url(url):
        logging.warning(f"Invalid URL for QR code generation: {url}")
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
    # image_to_base64 now expects PIL.Image.Image or a path, not a qrcode.image.pil.PilImage directly
    # So we convert it to a standard PIL Image object first.
    # However, qr_image should already be a PIL.Image.Image, so direct pass is fine.
    b64_qrcode = await image_to_base64(qr_image)
    return b64_qrcode

# Moved from utils.py
async def get_and_crop_image(src: str, output_path: str, width: int = 700, is_html: bool = False):
    """
    Fetches an image (from URL or local path) and crops it.
    This function DOES NOT render HTML to an image.
    If is_html is True, it implies 'src' is a path to an image file that was previously rendered from HTML,
    or this flag is used by a caller to indicate the origin/type of the image being processed.
    The primary purpose is to fetch remote images or load local images, then crop and save.
    """
    try:
        if src.startswith(("http://", "https://")):
            async with aiohttp.ClientSession() as session:
                async with session.get(src, timeout=10) as response:
                    if response.status != 200:
                        logging.error(f"Failed to download image from {src}, status: {response.status}")
                        return
                    data = await response.read()
                    image = PIL.Image.open(io.BytesIO(data))
        elif os.path.exists(src): # Local file path (assumed to be an image)
            if is_html:
                # This function is not for rendering HTML to image.
                # 'src' is expected to be a path to an *image file*.
                # The 'is_html' flag might indicate the conceptual origin of this image file.
                logging.info(f"Processing image file (originally from HTML source): {src}")
            image = PIL.Image.open(src)
        else:
            logging.error(f"Image source not found: {src}")
            return

        w, h = image.size
        cropped = image.crop((0, 0, min(width, w), h)) # Simple crop to width, keeping original height
        cropped.save(output_path)
        logging.info(f"Image saved to {output_path} after processing from {src}")
    except aiohttp.ClientError as e:
        logging.error(f"Aiohttp client error processing image {src}: {e}")
    except FileNotFoundError:
        logging.error(f"File not found during image processing: {src} or {output_path}")
    except PIL.UnidentifiedImageError:
        logging.error(f"Cannot identify image file: {src}")
    except Exception as e:
        logging.error(f"Error processing image {src} to {output_path}: {e}")


class HTMLRenderer:
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 2

    def __init__(self, html_template_str: str, logo_path: str, html_render_func: Callable):
        self.html_template_str = html_template_str
        self.logo_path = logo_path
        self.html_render_func = html_render_func # Expected: async def func(template_str, data, to_file_path_or_false_for_bytes)

    async def render_html_to_image(self, render_data: Dict[str, Any], output_image_path: str, temp_html_path_prefix: str = "temp_render"):
        # 1. Ensure avatar is present, default to logo if not
        if not render_data.get("avatar"):
            try:
                render_data["avatar"] = await image_to_base64(self.logo_path)
                logging.info(f"Avatar missing, defaulted to logo: {self.logo_path}")
            except Exception as e:
                logging.error(f"Failed to load logo as default avatar: {e}")
                render_data["avatar"] = "" # Fallback to empty if logo fails

        # 2. (Optional) Default primary image if image_urls is empty and template expects one
        # This logic is highly template-dependent. For now, we'll assume the template
        # can handle empty image_urls. If a specific field like 'primary_image' was
        # required, it would be handled here.
        # Example:
        # if not render_data.get("image_urls") and render_data.get("needs_primary_image_field"):
        #     render_data["primary_image_field"] = await image_to_base64(self.logo_path)

        temp_html_file = None # Initialize to ensure it's defined for finally block

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                # Generate a unique temp HTML file name for each attempt if needed, or manage overwrite
                # The html_render_func is expected to take the template string and data,
                # and a path where it should save the rendered HTML (or image directly).
                # Based on the problem description, html_render_func(template, data, False)
                # might imply it returns bytes, or it saves to a path if a path is given.
                # The problem states: "await self.html_render_func(self.html_template_str, render_data, False)"
                # and then "After rendering the HTML to a temporary file path (`src`)"
                # This implies html_render_func might save to a temp file itself, or we make one.
                # Let's assume html_render_func can save to a path if provided.

                # Let's refine the interaction with html_render_func.
                # If html_render_func is playright's page.screenshot, it takes a path.
                # If it's jinja2 rendering, it produces a string, which we then write to file.
                # The subtask implies html_render_func itself handles the browser rendering to an image.
                # "await self.html_render_func(self.html_template_str, render_data, False)"
                # This call seems to be from an old context. The `html_render_func` is likely the browser rendering call.

                # The new understanding:
                # 1. Populate render_data (caller does most of this).
                # 2. This method prepares a complete HTML string using self.html_template_str and render_data.
                #    (This step seems missing - html_template_str is just a string, needs filling)
                #    Let's assume render_data is ALREADY the complete HTML string or that
                #    self.html_render_func takes the template and data separately and does the Jinja part.
                #    The original `html_render.py` has `async def render(url, data, to_file=False)` which suggests
                #    `url` is the template path/URL, and `data` is for filling it.
                #    Let's assume self.html_template_str IS the template content, and html_render_func
                #    is like `page.set_content(html_filled_template)` then `page.screenshot()`.

                # For now, let's assume self.html_render_func is a high-level function that takes
                # the raw template string, the data, and an output path for the image.
                # Signature: async def html_render_func(template_str: str, data: dict, output_path: str)

                # If html_render_func needs a temporary HTML file first:
                # temp_html_file = f"{temp_html_path_prefix}_{os.urandom(8).hex()}.html"
                # filled_html = self.html_template_str.format(**render_data) # Simplistic, needs Jinja or similar
                # with open(temp_html_file, "w", encoding="utf-8") as f:
                # f.write(filled_html)
                # await self.html_render_func(temp_html_file_path_as_url, output_image_path)

                # Re-reading the prompt: "await self.html_render_func(self.html_template_str, render_data, False)"
                # This `False` argument is confusing.
                # The description for `html_render.py` (the likely source of `html_render_func`):
                # `async def render(url, data, to_file=False)`
                # - `url`: path to HTML template file.
                # - `data`: for Jinja2.
                # - `to_file=False`: if True, returns path to image; if False, returns image bytes.
                # So, `self.html_template_str` should be a *path* to the template, or we write it to a temp file.
                # And `render_data` is for Jinja.

                # Let's assume self.html_template_str IS the template *content*.
                # We need to write it to a temporary file to pass to html_render_func if it expects a path.
                
                temp_template_file_for_render_func = f"{temp_html_path_prefix}_template_{os.urandom(8).hex()}.html"
                with open(temp_template_file_for_render_func, "w", encoding="utf-8") as f:
                    f.write(self.html_template_str)

                # Call the provided html_render_func. It's expected to:
                # 1. Use Jinja2 with temp_template_file_for_render_func and render_data.
                # 2. Render this HTML in a browser.
                # 3. Save the screenshot to output_image_path.
                # The `False` argument implies it should return bytes, but we need a file.
                # Let's assume the interface is `await self.html_render_func(template_path, data_dict, output_image_path_str)`
                
                # The prompt: "await self.html_render_func(self.html_template_str, render_data, False)"
                # then "After rendering the HTML to a temporary file path (`src`)"
                # This is contradictory.
                # Let's assume `html_render_func` *is* the playwright call that produces the image directly.
                # And `self.html_template_str` is the *already filled* HTML content or a path to it.
                # Or, more likely, the `html_render_func` is the one from `html_render.py` which does Jinja internally.

                # Scenario: html_render_func is `astrophotobot.utils.html_render.render`
                # async def render(template_path: str, data: dict, output_path: str)
                # This function will:
                # 1. Read `template_path`, fill with `data` using Jinja.
                # 2. Save filled HTML to a temp file.
                # 3. Use Playwright to open this temp HTML and save screenshot to `output_path`.

                # So, we must first write self.html_template_str to a temp file.
                temp_template_path = f"{temp_html_path_prefix}_base_template_{os.urandom(8).hex()}.html"
                try:
                    with open(temp_template_path, "w", encoding="utf-8") as f:
                        f.write(self.html_template_str)
                    
                    logging.info(f"Attempt {attempt}: Rendering HTML to {output_image_path} using template {temp_template_path}")
                    # The html_render_func is expected to handle Jinja itself, take template path, data, and output image path.
                    await self.html_render_func(template_path=temp_template_path, data=render_data, output_path=output_image_path)

                    # The prompt then says: "it will use get_and_crop_image ... to convert this HTML file to output_image_path"
                    # This is also contradictory if html_render_func already produces output_image_path.
                    # Let's assume html_render_func produces the image at output_image_path,
                    # AND THEN get_and_crop_image is optionally used if cropping is needed on this output_image_path.
                    # The current get_and_crop_image takes src and output_path.
                    # For now, no explicit cropping call after rendering, assuming html_render_func produces the final image.
                    # If cropping is needed:
                    # temp_raw_image = output_image_path + ".raw.png"
                    # os.rename(output_image_path, temp_raw_image)
                    # await get_and_crop_image(temp_raw_image, output_image_path, width=...)
                    # os.remove(temp_raw_image)

                    if os.path.exists(output_image_path) and os.path.getsize(output_image_path) > 0:
                        logging.info(f"HTML rendered successfully to {output_image_path} on attempt {attempt}")
                        return # Success
                    else:
                        logging.warning(f"Attempt {attempt}: Output image not found or empty at {output_image_path}")
                        # This will be caught by the generic Exception or lead to retry
                finally:
                    if os.path.exists(temp_template_path):
                        os.remove(temp_template_path)


            except Exception as e:
                logging.error(f"Attempt {attempt} failed to render HTML to image: {e}")
                if attempt < self.MAX_ATTEMPTS:
                    logging.info(f"Retrying in {self.RETRY_DELAY} seconds...")
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    logging.error(f"All {self.MAX_ATTEMPTS} attempts failed.")
                    raise # Re-raise the last exception if all attempts fail
            finally:
                # Cleanup temporary HTML file if one was created by this function directly
                # (temp_template_path is cleaned up above)
                # If html_render_func creates its own temp files, it should clean them.
                pass
        
        # Should not be reached if success returns or error raises
        raise Exception("Rendering failed after all retries, but no exception was propagated.")
