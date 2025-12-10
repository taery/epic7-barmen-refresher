import csv
import random
import threading
import time
from datetime import datetime
import os
# For GUI
import tkinter as tk
from tkinter import ttk

import numpy as np
import pyautogui

# WORK with images
import cv2
from PIL import ImageTk, Image, ImageDraw

# Work with macOS app windows
from atomacos import NativeUIElement, getAppRefByBundleId


class AppConfig:
    def __init__(self):
        self.DEBUG = True

        # general setting
        self.app_title = 'Epic Seven'
        # list of all the purchasable item
        self.ALL_ITEMS = [['cov2.png', 'Covenant bookmark', 184000, 'cov_buy.png'],
                          ['mys.png', 'Mystic medal', 280000, 'mys_buy.png']]
        # self.MANDATORY_PATH = {'cov.png', 'mys.png'}  # make item unable to be unselected

        # gui
        # color
        self.unite_bg_color = '#171717'
        self.unite_text_color = '#dddddd'

        # Refresher defaults
        self.mouse_speed = 0.3
        self.screenshot_speed = 0.3
        self.budget = 100
        self.skip_items = set()


def find_window(title) -> NativeUIElement | None:
    system = getAppRefByBundleId("com.stove.epic7.ios")
    return next(iter(system.windows(match=title)), None)


def safe_get_window_param(window) -> tuple[int, int, int, int]:
    left, top = window.AXPosition
    width, height = window.AXSize
    return int(left), int(top), int(width), int(height)


def get_relative_path(file_name):
    return os.path.join('assets', file_name)


def validate_float(value, action):
    if action != '1':
        return True

    try:
        return 0 <= float(value) <= 10
    except:
        return False


def validate_int(value):
    if value == '':
        return True

    try:
        int_value = int(value)
    except:
        return False

    return 0 <= int_value < 100000000


def safe_locate_center_button_on_screen(image_path, confidence=0.8) -> pyautogui.Point | None:
    try:
        return pyautogui.locateCenterOnScreen(image_path, confidence=confidence)
    except Exception as e:
        print('No button found on screen:', image_path)
        print(e)
        return None


class ShopItem:
    def __init__(self, path='', image=None, price=0, count=0, buy_button=''):
        self.path = path
        self.image = image
        self.price = price
        self.count = count
        self.buy_button = buy_button

    def __repr__(self):
        return (f'ShopItem(path={self.path}, image={self.image}, price={self.price}, count={self.count}, '
                f'buy_button={self.buy_button})')


class RefreshStatistic:
    def __init__(self):
        self.refresh_count = 0
        self.items = {}
        self.start_time = datetime.now()

    def update_time(self):
        self.start_time = datetime.now()

    def add_shop_item(self, path: str, name='', price=0, count=0):
        image = Image.open(get_relative_path(path))
        image = image.resize((45, 45))
        image = ImageTk.PhotoImage(image)
        self.items[name] = ShopItem(path, image, price, count)

    def get_inventory(self):
        return self.items

    def get_names(self):
        return list(self.items.keys())

    def get_images(self):
        return [shop_item.image for shop_item in self.items.values()]

    def get_paths(self):
        return [shop_item.path for shop_item in self.items.values()]

    def get_item_counts(self):
        return [shop_item.count for shop_item in self.items.values()]

    def get_total_cost(self):
        total = 0
        for shop_item in self.items.values():
            total += shop_item.price * shop_item.count
        return total

    def increment_refresh_count(self):
        self.refresh_count += 1

    def write_to_csv(self):
        res_folder = 'ShopRefreshHistory'
        if not os.path.exists(res_folder):
            os.makedirs(res_folder)

        gen_path = 'refreshAttempt'
        for name in self.get_names():
            gen_path += name[:4]
        gen_path += '.csv'

        path = os.path.join(res_folder, gen_path)

        if not os.path.isfile(path):
            with open(path, 'w', newline='') as file:
                writer = csv.writer(file)
                column_name = ['Time', 'Duration', 'Refresh count', 'Skystone spent', 'Gold spent']
                column_name.extend(self.get_names())
                writer.writerow(column_name)

        with open(path, 'a', newline='') as file:
            writer = csv.writer(file)
            data = [self.start_time, datetime.now() - self.start_time, self.refresh_count, self.refresh_count * 3,
                    self.get_total_cost()]
            data.extend(self.get_item_counts())
            writer.writerow(data)


class SecretShopRefresh:
    def __init__(self, title_name: str, callback=None, root_window: tk = None, budget: int = None,
                 debug: bool = False):
        # init state
        self.debug = debug
        self.mouse_sleep = 0.3
        self.screenshot_sleep = 0.3
        self.callback = callback if callback else self.refresh_finish_callback
        self.budget = budget

        # find window
        self.window: NativeUIElement = find_window(title_name)
        self.root_window = root_window
        self.statistic_calculator = RefreshStatistic()

        # stop control and worker thread
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # Start shop refresh macro
    def start(self):
        print('Starting refreshing ...')

        refresh_thread = threading.Thread(target=self.shop_refresh_loop)
        refresh_thread.daemon = True
        refresh_thread.start()

        self._thread = refresh_thread

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        print('Terminating shop refresh ...')

    def _run_loop(self):
        try:
            if hasattr(self, 'shop_refresh_loop') and callable(getattr(self, 'shop_refresh_loop')):
                try:
                    self.shop_refresh_loop()
                except Exception as e:
                    print('shop_refresh_loop error:', e)
            else:
                print('shop_refresh_loop not implemented.')
        finally:
            # Ensure callback runs on Tk main thread if root_window provided
            try:
                if self.root_window:
                    self.root_window.after(0, self.callback)
                else:
                    self.callback()
            except Exception:
                self.callback()

    def refresh_finish_callback(self):
        print('Refresher Terminated!')

    def shop_refresh_loop(self):
        print('Start shop refreshing loop ...')
        self.window.AXRaise()

        # TODO close on esc?
        # show mini display
        # generating mini image
        hint, mini_labels = self.show_mini_display()

        # update state on minidisplay
        def update_mini_display():
            for label, count in zip(mini_labels, self.statistic_calculator.get_item_counts()):
                label.config(text=count)

        def search_and_buy():
            if self.debug: print('Searching for items to buy ...')
            for key, shop_item in self.statistic_calculator.get_inventory().items():
                if key in bought:
                    continue
                if self.debug: print('Searching for item:', key)
                item_pos = safe_locate_center_button_on_screen(get_relative_path(shop_item.path))
                if item_pos is not None:
                    if self.debug: print(f'Found item {key} at:', item_pos)
                    self.click_button(shop_item.buy_button)
                    # Confirm buy
                    self.click_button(shop_item.buy_button)
                    shop_item.count += 1
                    bought.add(key)
                    if hint: update_mini_display()

        time.sleep(self.mouse_sleep)

        try:
            self.statistic_calculator.update_time()
            # item sliding const
            sliding_time = max(0.7 + self.screenshot_sleep, 1)

            # Loop through shop
            while not self._stop_event.is_set():
                # array for determining if an item has been purchased in this loop
                bought = set()

                # take screenshot, check for items, buy all items that appear
                time.sleep(sliding_time)  # This is a constant sleep to account for the item sliding in frame

                ###start of bundle refresh
                if self.debug: print('start of bundle refresh')
                # Search for items to buy
                search_and_buy()
                self.scroll_up()
                # Search for items to buy after scrolling up
                search_and_buy()
                self.scroll_down()
                # Search for items to buy after scrolling up
                search_and_buy()

                if self.debug: print('Finished searching for items to buy, refresh shop now.')

                if self.debug: time.sleep(5)
                if self._stop_event.is_set() or self.budget and self.statistic_calculator.refresh_count >= self.budget:
                    break

                self.click_refresh()
                self.statistic_calculator.increment_refresh_count()
                time.sleep(self.mouse_sleep)
        except Exception as e:
            print(e)
            if hint: hint.destroy()
            self.statistic_calculator.write_to_csv()
            self.callback()
            return

        if hint: hint.destroy()
        self.statistic_calculator.write_to_csv()
        self.callback()

    # show mini display
    def show_mini_display(self):
        bg_color = '#171717'
        fg_color = '#dddddd'

        if self.root_window is None:
            return None, None

        # Display exit key
        hint = tk.Toplevel(self.root_window)

        pos = self.window.AXPosition
        hint.geometry(r'200x200+%d+%d' % (pos[0], pos[1] + self.window.AXSize[1]))
        hint.title('Hint')

        # Attach to main window (do not grab focus) and ensure main window regains focus
        try:
            hint.transient(self.root_window)
        except Exception:
            print('Show mini window failed.')

        tk.Label(master=hint, text='Press ESC to stop refreshing!', bg=bg_color, fg=fg_color).pack()
        hint.config(bg=bg_color)

        # Display stat
        mini_stats = tk.Frame(master=hint, bg=bg_color)
        mini_labels = []

        # packing mini image
        for img in self.statistic_calculator.get_images():
            frame = tk.Frame(mini_stats, bg=bg_color)
            tk.Label(master=frame, image=img, bg=bg_color).pack(side=tk.LEFT)
            count = tk.Label(master=frame, text='0', bg=bg_color, fg='#FFBF00')
            count.pack(side=tk.RIGHT)
            mini_labels.append(count)
            frame.pack()
        mini_stats.pack()
        return hint, mini_labels

    # add item to list
    def add_search_item(self, path: str, name='', price=0, count=0):
        print("Adding search item:", name)
        self.statistic_calculator.add_shop_item(path, name, price, count)

    # Add randomness to click position
    def click_button(self, button_url):
        button_center = safe_locate_center_button_on_screen(get_relative_path(button_url))
        if not button_center: raise Exception(f'Button {button_url} not found on the screen.')

        if self.debug: print('Found button at:', button_center)

        rand_x = random.randint(-3, 3) + button_center.x
        rand_y = random.randint(-3, 3) + button_center.y

        # scale click position to logical screen coords
        screen_w, screen_h = pyautogui.size()
        full_img = pyautogui.screenshot()
        img_w, img_h = full_img.size
        scale_x = screen_w / img_w if img_w else 1.0
        scale_y = screen_h / img_h if img_h else 1.0

        rand_x = int(rand_x * scale_x)
        rand_y = int(rand_y * scale_y)

        pyautogui.moveTo(rand_x, rand_y, duration=self.mouse_sleep)
        if self.debug: print('Moving button at:', (rand_x, rand_y))

        pyautogui.click(rand_x, rand_y, clicks=2, interval=self.mouse_sleep)
        if self.debug: print('Clicked button at:', (rand_x, rand_y))

        time.sleep(random.uniform(self.mouse_sleep - 0.1, self.mouse_sleep + 0.1))

    # # REFRESH MACRO
    def click_refresh(self):
        # click twice to open refresh menu?
        print('Clicking refresh button...')
        self.click_button('refresh_button.png')
        if self.debug: time.sleep(1)
        self.click_confirm_refresh()

    def click_confirm_refresh(self):
        self.click_button('confirm.png')
        time.sleep(random.uniform(self.screenshot_sleep - 0.1, self.screenshot_sleep + 0.1))  # Account for Loading

    def scroll_down(self):
        left, top, width, height = safe_get_window_param(self.window)

        start_x = left + width * 0.58
        start_y = top + height * 0.65
        end_y = start_y - height * 0.5

        # Move to start
        pyautogui.moveTo(start_x, start_y, duration=0.2)

        # Drag smoothly
        pyautogui.dragTo(start_x, end_y, duration=0.5, button='left')
        time.sleep(max(0.3, self.screenshot_sleep))

    def scroll_up(self):
        left, top, width, height = safe_get_window_param(self.window)

        start_x = left + width * 0.58
        start_y = top + height * 0.65
        end_y = start_y + height * 0.5  # move down to scroll up

        # Move to start position
        pyautogui.moveTo(start_x, start_y, duration=0.2)

        # Drag smoothly
        pyautogui.dragTo(start_x, end_y, duration=0.5, button='left')
        time.sleep(max(0.3, self.screenshot_sleep))


class RefresherGUI:
    def __init__(self):

        self.app_config = AppConfig()
        self.settings_window = tk.Tk()

        #  init settings window
        self.settings_window.config(bg=self.app_config.unite_bg_color)
        self.settings_window.title('SHOP AUTO REFRESH')
        self.settings_window.geometry('420x745')
        self.settings_window.minsize(420, 745)
        self.permanent_icons = []

        self.settings_window.bind_all('<Escape>', self.stop_shop_refresh)

        settings_app_title = tk.Label(self.settings_window, text='Epic Seven shop refresh',
                                      font=('Helvetica', 24),
                                      bg=self.app_config.unite_bg_color,
                                      fg=self.app_config.unite_text_color)

        self.lock_start_button = False

        def pack_label(text, text_size=14, pady=10):
            new_label = tk.Label(self.settings_window, text=text, font=('Helvetica', text_size),
                                 bg=self.app_config.unite_bg_color,
                                 fg=self.app_config.unite_text_color)
            new_label.pack(pady=pady)
            return new_label

        def pack_item(item):
            path = item[0]

            def update_skip_items():
                print('Updating skip item:', path)
                self.lock_start_button = True
                if item_checkbox_value.get() == 1:
                    self.app_config.skip_items.discard(path)
                else:
                    self.app_config.skip_items.add(path)
                print(self.app_config.skip_items)

            item_checkbox_value = tk.IntVar()
            frame = tk.Frame(self.settings_window,
                             bg=self.app_config.unite_bg_color,
                             pady=10)

            item_checkbox = tk.Checkbutton(master=frame, variable=item_checkbox_value,
                                           command=update_skip_items,
                                           bg=self.app_config.unite_bg_color)
            item_checkbox.pack(side=tk.LEFT)
            if path not in self.app_config.skip_items:
                item_checkbox.select()
            icon = ImageTk.PhotoImage(image=Image.open(get_relative_path(path)))
            self.permanent_icons.append(icon)

            image_label = tk.Label(master=frame, image=icon, bg='#FFBF00')
            image_label.pack(side=tk.RIGHT)
            frame.pack()

        def pack_setting_entry(text, default_value=0.0):
            frame = tk.Frame(additional_setting_frame, bg=self.app_config.unite_bg_color, pady=4)
            label = tk.Label(master=frame,
                             text=text,
                             bg=self.app_config.unite_bg_color,
                             fg=self.app_config.unite_text_color,
                             font=('Helvetica', 12))  # apply ui change here
            entry = tk.Entry(master=frame,
                             bg='#333333',
                             fg=self.app_config.unite_text_color,
                             font=('Helvetica', 12),
                             width=10)
            label.pack(side=tk.LEFT)

            if default_value or abs(default_value) < 1e-9:
                entry.insert(0, str(default_value))

            entry.pack(side=tk.RIGHT)
            frame.pack()
            return entry

        game_app_value = tk.StringVar()
        game_app_name = ttk.Entry(master=self.settings_window, textvariable=game_app_value, state=tk.DISABLED)

        # UI from top to down
        settings_app_title.pack(pady=(15, 0))

        ## Step 1 Detect the game app
        pack_label('Type emulator\'s window title if not detected:')
        game_app_name.pack()

        ## Step 2 Select item
        pack_label('Select items that you are looking for:')
        for i in self.app_config.ALL_ITEMS:
            pack_item(i)

        pack_label('Setting:', 18, (10, 0))

        ## Step 3 Select setting
        # setting frame
        additional_setting_frame = tk.Frame(self.settings_window)
        additional_setting_frame.config(bg=self.app_config.unite_bg_color)

        validate_float_command = self.settings_window.register(validate_float)
        self.mouse_speed_entry = pack_setting_entry('Mouse speed (s):',
                                                    self.app_config.mouse_speed)
        self.screenshot_speed_entry = pack_setting_entry('Screenshot speed (s):',
                                                         self.app_config.screenshot_speed)
        self.mouse_speed_entry.config(validate='key', validatecommand=(validate_float_command, '%P', '%d'))
        self.screenshot_speed_entry.config(validate='key', validatecommand=(validate_float_command, '%P', '%d'))

        validate_integer_command = self.settings_window.register(validate_int)
        self.limit_spend_entry = pack_setting_entry('How many skystone do you want to spend? :',
                                                    self.app_config.budget)
        self.limit_spend_entry.config(validate='key', validatecommand=(validate_integer_command, '%P'))

        additional_setting_frame.pack()

        ## Step 4 profit
        # start refreshing button
        self.start_button = tk.Button(master=self.settings_window,
                                      text='Start refresh',
                                      font=('Helvetica', 14),
                                      state=tk.DISABLED,
                                      command=self.start_shop_refresh)

        # check if recognize titles match with any window
        title = self.app_config.app_title
        if find_window(title):
            game_app_value.set(title)
        else:
            game_app_value.set('Failed to detect window')
            self.lock_start_button = True

        if not self.lock_start_button:
            self.start_button.config(state=tk.NORMAL)

        self.start_button.pack(pady=(30, 0))

        self.settings_window.mainloop()

    def stop_shop_refresh(self, event=None):
        print('Shop Refresh stop called')
        self.settings_window.destroy()
        # Called when Escape pressed; signal refresher to stop and restore UI
        if hasattr(self, 'ssr') and hasattr(self.ssr, 'stop'):
            try:
                self.ssr.stop()
            except Exception:
                print('Failed to stop ShopRefresh')
        # Ensure UI is updated (callback may also update it)
        self.refresh_complete()

    def refresh_complete(self):
        print('Terminated!')
        self.settings_window.title('SHOP AUTO REFRESH')
        self.start_button.config(state=tk.NORMAL)
        self.lock_start_button = False

    # start refresh loop
    def start_shop_refresh(self):
        self.settings_window.title('Press ESC to stop!')
        self.lock_start_button = True
        self.start_button.config(state=tk.DISABLED)
        self.ssr = SecretShopRefresh(title_name=self.app_config.app_title, callback=self.refresh_complete,
                                     debug=self.app_config.DEBUG)

        self.ssr.root_window = self.settings_window

        # setting item to search while refreshing
        for item in self.app_config.ALL_ITEMS:
            if item[0] not in self.app_config.skip_items:
                self.ssr.add_search_item(path=item[0], name=item[1], price=item[2])

        # setting additional settings
        self.ssr.mouse_sleep = float(
            self.mouse_speed_entry.get()
        ) if self.mouse_speed_entry.get() != '' else self.app_config.mouse_speed
        self.ssr.screenshot_sleep = float(
            self.screenshot_speed_entry.get()
        ) if self.screenshot_speed_entry.get() != '' else self.app_config.screenshot_speed

        # More validation?
        self.ssr.mouse_sleep = max(0.01, self.ssr.mouse_sleep)
        self.ssr.screenshot_sleep = max(0.01, self.ssr.screenshot_sleep)

        # setting up skystone budget
        if self.limit_spend_entry.get() != '':
            self.ssr.budget = int(self.limit_spend_entry.get())

        print('refresh shop start!')
        print('Budget:', self.ssr.budget)
        print('Mouse speed:', self.ssr.mouse_sleep)
        print('Screenshot speed', self.ssr.screenshot_sleep)
        self.ssr.start()


if __name__ == '__main__':
    g = RefresherGUI()
    print('started')
