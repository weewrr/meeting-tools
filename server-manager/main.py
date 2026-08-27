# -*- coding: utf-8 -*-
"""服务器管理器入口：python main.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    from ui import App
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
