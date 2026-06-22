"""tdx_local - 通达信本地 vipdoc 数据读取（日线/.lc5/.lc1），含 pytdx 在线回退"""
from .reader import TdxLocalReader
__all__ = ["TdxLocalReader"]
