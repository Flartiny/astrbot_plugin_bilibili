import logging
from bilibili_api import Credential, user, video, bangumi

class BilibiliApiClient:
    def __init__(self, credential: Credential = None):
        if credential is None:
            logging.warning("Credential is not provided. Some APIs may not work.")
        self.credential = credential

    async def get_video_info_api(self, bvid: str):
        try:
            v = video.Video(bvid=bvid, credential=self.credential)
            info = await v.get_info()
            online = await v.get_online()
            return {"info": info, "online": online}
        except Exception as e:
            logging.error(f"Error getting video info for {bvid}: {e}")
            return None

    async def get_user_info_api(self, uid: int):
        try:
            u = user.User(uid=uid, credential=self.credential)
            user_info = await u.get_user_info()
            return user_info
        except Exception as e:
            logging.error(f"Error getting user info for {uid}: {e}")
            return None

    async def get_user_dynamics_api(self, uid: int):
        try:
            u = user.User(uid=uid, credential=self.credential)
            dynamics = await u.get_dynamics_new()
            return dynamics
        except Exception as e:
            logging.error(f"Error getting user dynamics for {uid}: {e}")
            return None

    async def get_user_live_info_api(self, uid: int):
        try:
            u = user.User(uid=uid, credential=self.credential)
            live_info = await u.get_live_info()
            return live_info
        except Exception as e:
            logging.error(f"Error getting user live info for {uid}: {e}")
            return None

    async def get_bangumi_api(self, filters, order, sort, pn=1, ps=5):
        try:
            index_info = await bangumi.get_index_info(
                filters=filters, order=order, sort=sort, page_num=pn, page_size=ps
            )
            return index_info
        except Exception as e:
            logging.error(f"Error getting bangumi index info: {e}")
            return None
