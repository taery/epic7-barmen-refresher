import os
import csv
import random
import threading
import time
from datetime import datetime
from typing import Callable
# For GUI
import tkinter as tk
from tkinter import ttk


import cv2
import mss
import numpy as np
import pyautogui

# WORK with images
from PIL import ImageTk, Image, ImageGrab

# Work with macOS app windows
from atomacos import NativeUIElement, getAppRefByBundleId


class AppConfig:
    def __init__(self):
        self.DEBUG = False

        # general setting
        self.app_title = 'Epic Seven'
        # list of all the purchasable item
        self.ALL_ITEMS = [
            ['mys.png', 'Mystic medal', 280000],
            ['cov.png', 'Covenant bookmark', 184000],
                          ]

        # gui
        # color
        self.unite_bg_color = '#171717'
        self.unite_text_color = '#dddddd'

        # Refresher defaults
        self.mouse_speed = 0.3
        self.screenshot_speed = 0.3
        self.budget = 100
        self.skip_items = set()


def activate_game():
    """
    Activate application using PyObjC (most reliable method).
    """
    try:
        from Cocoa import NSWorkspace, NSApplicationActivateIgnoringOtherApps

        workspace = NSWorkspace.sharedWorkspace()
        apps = workspace.runningApplications()
        bundle_id = 'com.stove.epic7.ios'

        target_app = next(
            (app for app in apps if app.bundleIdentifier() == ('%s' % bundle_id)),
            None
        )
        if target_app is None:
            print(f"❌ Application {bundle_id} is not running")
            return False

        # Activate with option to ignore other apps (force to front)
        success = target_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        return success
    except ImportError:
        print("⚠️  PyObjC not installed, falling back to AppleScript")
        return False
    except Exception as e:
        print(f"⚠️  PyObjC activation failed: {e}")
        return False

def find_window(title) -> NativeUIElement | None:
    system = getAppRefByBundleId("com.stove.epic7.ios")
    return next(iter(system.windows(match=title)), None)


def safe_get_window_param(window) -> tuple[int, int, int, int]:
    left, top = window.AXPosition
    width, height = window.AXSize
    return int(left), int(top), int(width), int(height)


def get_relative_path(file_name):
    if not file_name:
        raise Exception("No file name provided")
    return os.path.join('assets', file_name)


def validate_float(value, action):
    if action != '1':
        return True
    try:
        return 0 <= float(value) <= 10
    except ValueError:
        return False


def validate_int(value):
    if value == '':
        return True

    try:
        int_value = int(value)
    except ValueError:
        return False

    return 0 <= int_value < 100000000


class ShopItem:
    def __init__(self, path='', show_image=None, search_image=None, price=0, count=0):
        self.path = path
        self.show_image = show_image
        self.search_image = search_image
        self.price = price
        self.count = count

    def __repr__(self):
        return (f'ShopItem(path={self.path}, show_image={self.show_image}, search_image={self.search_image},'
                f' price={self.price}, count={self.count}')


class RefreshStatistic:
    def __init__(self):
        self.refresh_count = 0
        self.items = {}
        self.start_time = datetime.now()

    def update_time(self):
        self.start_time = datetime.now()

    def add_shop_item(self, path: str, name='', price=0, count=0):
        relative_path = get_relative_path(path)
        image = Image.open(relative_path).resize((45, 45))
        image = ImageTk.PhotoImage(image)

        image2 = cv2.imread(relative_path)
        image2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)
        # if path == 'mys.png':
        #     image2 = cv2.resize(image2, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_LINEAR)
        image2 = cv2.resize(image2, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_LINEAR)
        self.items[name] = ShopItem(path, show_image=image, search_image=image2, price=price, count=count)

    def get_inventory(self):
        return self.items

    def get_names(self):
        return list(self.items.keys())

    def get_show_images(self):
        return [_.show_image for _ in self.items.values()]

    def get_paths(self):
        return [_.path for _ in self.items.values()]

    def get_item_counts(self):
        return [_.count for _ in self.items.values()]

    def get_total_cost(self):
        return sum(_.price * _.count for _ in self.items.values())

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
                column_names = ['Time', 'Duration', 'Refresh count', 'Skystone spent', 'Gold spent']
                column_names.extend(self.get_names())
                writer.writerow(column_names)

        with open(path, 'a', newline='') as file:
            writer = csv.writer(file)
            data = [self.start_time, datetime.now() - self.start_time, self.refresh_count, self.refresh_count * 3,
                    self.get_total_cost()]
            data.extend(self.get_item_counts())
            writer.writerow(data)


class SecretShopRefresh:
    def __init__(self, title_name: str, terminate_callback: Callable[[], None], settings_window: tk = None,
                 budget: int = None,
                 debug: bool = False):
        # init state
        self.debug = debug
        self.debug_screenshot = False
        self.is_stop_refresh = False
        self.mouse_sleep = 0.3
        self.screenshot_sleep = 0.3
        self.terminate_callback = terminate_callback
        self.budget = budget

        # find window
        self.game_window: NativeUIElement = find_window(title_name)
        self.settings_window = settings_window
        self.statistic_calculator = RefreshStatistic()

        # stop control and worker thread
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Global keyboard listener for ESC key
        self._event_monitor = None
        self._esc_check_thread = None

    def _check_esc_key_macos(self):
        """
        macOS-native ESC key monitoring using Cocoa events.
        Runs in a separate thread.
        """
        from Cocoa import NSEvent, NSEventMaskKeyDown
        import Quartz

        def callback(proxy, event_type, event, refcon):
            """Callback for global event monitor."""
            try:
                # Check if ESC key (keycode 53)
                keycode = Quartz.CGEventGetIntegerValueField(
                    event,
                    Quartz.kCGKeyboardEventKeycode
                )

                if keycode == 53:  # ESC key
                    print("🛑 ESC pressed - stopping refresh...")
                    self._stop_event.set()

            except Exception as e:
                if self.debug:
                    print(f"Event monitor error: {e}")

            # Return event to allow normal processing
            return event

        # Create event tap
        try:
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
                callback,
                None
            )

            if tap is None:
                print("⚠️ Failed to create event tap - accessibility permissions may be needed")
                return

            # Create run loop source
            run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetCurrent(),
                run_loop_source,
                Quartz.kCFRunLoopCommonModes
            )

            # Enable the tap
            Quartz.CGEventTapEnable(tap, True)

            # Run the loop
            Quartz.CFRunLoopRun()

        except Exception as e:
            print(f"Event tap error: {e}")


    def start(self):
        if self.debug: print('Starting refreshing ...')

        # Start macOS event monitor in separate thread
        self._esc_check_thread = threading.Thread(
            target=self._check_esc_key_macos,
            daemon=True
        )
        self._esc_check_thread.start()

        self._thread = threading.Thread(target=self.shop_refresh_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        print('Terminating shop refresh ...')

    def take_screenshot(self) -> np.ndarray:
        left, top, width, height = safe_get_window_param(self.game_window)
        region = [left, top, width, height]
        print('Taking screenshot at region:', region)
        screenshot = ImageGrab.grab(bbox=(left, top, left + width, top + height),
                                    all_screens=True)
        screenshot.save('debug_screenshot.png')
        screenshot = np.array(screenshot)
        screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        return screenshot

    def take_screenshot_mss(self) -> np.ndarray:
        """Capture the game window using mss for native-quality (Retina-safe) pixels."""
        left, top, width, height = safe_get_window_param(self.game_window)
        monitor = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        with mss.mss() as sct:
            sct_img = sct.grab(monitor)  # raw BGRA
            arr = np.array(sct_img)  # shape (h, w, 4)
            if arr.ndim == 3 and arr.shape[2] == 4:
                bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
            else:
                bgr = arr[..., :3]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            return gray

    def shop_refresh_loop(self):
        print('Start shop refreshing loop ...')
        activate_game()
        # Show statistics widget
        hint, mini_labels, refresh_label = self.show_statistics_widget()

        def update_statistics_widget():
            for label, count in zip(mini_labels, self.statistic_calculator.get_item_counts()):
                label.config(text=count)

        def search_and_buy():
            if self._stop_event.is_set():  # Check for stop at start
                return

            if self.debug: print('Searching for items to buy ...')

            time.sleep(self.screenshot_sleep)
            screenshot = self.take_screenshot_mss()

            for key, shop_item in self.statistic_calculator.get_inventory().items():
                if self._stop_event.is_set():  # Check during iteration
                    return

                if key in bought: continue
                if self.debug: print('Searching for item:', key)

                item_pos = self.search_item(screenshot, shop_item)

                if item_pos is not None:
                    if self.debug: print(f'Found item {key} at:', item_pos)

                    if self._stop_event.is_set():  # Check before clicking
                        return

                    if self.click_buy(item_pos):
                        shop_item.count += 1
                        bought.add(key)

                    if hint: update_statistics_widget()

        time.sleep(self.mouse_sleep)

        try:
            self.statistic_calculator.update_time()
            sliding_time = max(0.7 + self.screenshot_sleep, 1)

            # Loop through shop
            while not self._stop_event.is_set():
                bought = set()

                time.sleep(sliding_time)

                if self.debug: print('start of bundle refresh')

                # Check for stop before each major operation
                if self._stop_event.is_set():
                    break

                search_and_buy()

                if self._stop_event.is_set():
                    break

                self.scroll_down()

                if self._stop_event.is_set():
                    break

                search_and_buy()

                if self.debug: print(f'Finished searching for items to buy, bought {bought} items, refresh shop now.')
                if self.debug: time.sleep(5)

                if self._stop_event.is_set() or (self.budget and
                                                 self.statistic_calculator.refresh_count >= self.budget):
                    break

                if not self.is_stop_refresh: self.click_refresh()
                self.statistic_calculator.increment_refresh_count()
                if hint: refresh_label.config(text=str(self.statistic_calculator.refresh_count))
                time.sleep(self.mouse_sleep)

        except Exception as e:
            print(f"Error in shop_refresh_loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if hint: hint.destroy()
            self.statistic_calculator.write_to_csv()

            self.terminate_callback()

    def show_statistics_widget(self):
        bg_color = '#171717'
        fg_color = '#dddddd'

        if self.settings_window is None:
            return None, None

        hint = tk.Toplevel(self.settings_window)
        pos = self.game_window.AXPosition

        hint.geometry(r'200x200+%d+%d' % (pos[0], pos[1] + self.game_window.AXSize[1]))
        hint.title('Hint')

        tk.Label(master=hint, text='Press ESC to stop refreshing!', bg=bg_color, fg=fg_color).pack()
        hint.config(bg=bg_color)

        refresh_frame = tk.Frame(master=hint, bg=bg_color)
        tk.Label(master=refresh_frame, text='Refresh count: ', bg=bg_color, fg=fg_color).pack(side=tk.LEFT)
        refresh_count_label = tk.Label(master=refresh_frame, text='0', bg=bg_color, fg='#FFBF00')
        refresh_count_label.pack(side=tk.RIGHT)
        refresh_frame.pack()

        # Display stat
        mini_stats = tk.Frame(master=hint, bg=bg_color)
        mini_labels = []

        # packing mini image
        for img in self.statistic_calculator.get_show_images():
            frame = tk.Frame(mini_stats, bg=bg_color)
            tk.Label(master=frame, image=img, bg=bg_color).pack(side=tk.LEFT)
            count = tk.Label(master=frame, text='0', bg=bg_color, fg='#FFBF00')
            count.pack(side=tk.RIGHT)
            mini_labels.append(count)
            frame.pack()
        mini_stats.pack()
        return hint, mini_labels, refresh_count_label

    def safe_locate_center_button_on_game_window(self, image_path, confidence=0.8) -> pyautogui.Point | None:
        try:
            print('Searching for button on screen:', image_path, self.debug)
            region = safe_get_window_param(self.game_window)

            box = pyautogui.locateOnScreen(image_path,
                                           region=region,
                                           confidence=confidence)
            print('Locating button on screen:', image_path, 'Found:', box)
            if not box:
                if self.debug: print('No button found on screen (debug):', image_path)
                return None

            center = pyautogui.center(box)
            if self.debug:
                try:
                    saved = save_debug_screenshot(image_path, box, center)
                    print('Saved debug screenshot to', saved)
                except Exception as e:
                    print('Failed to save debug screenshot:', e)

            return center
        except Exception as e:
            print('Failed to locate button on screen:', e)
            if self.debug: print('No button found on screen:', image_path)
        return None

    def add_search_item(self, path: str, name='', price=0, count=0):
        print("Adding search item:", name)
        self.statistic_calculator.add_shop_item(path, name, price, count)

    def click_buy(self, item_pos):
        if item_pos is None:
            return False
        # Calculate buy position based on item position
        left, top, width, height = safe_get_window_param(self.game_window)

        # Buy button is at 90% of width (your original calculation was correct)
        x = left + width * 0.90
        y = item_pos.y

        if self.debug: print('Buy item at position:', item_pos, (x, y))
        self.click_on_point(x, y)
        time.sleep(0.2)  # Small delay before confirming

        self.click_confirm_buy()
        return True

    def click_confirm_buy(self):
        left, top, width, height = safe_get_window_param(self.game_window)
        x = left + width * 0.55
        y = top + height * 0.70
        if self.debug: print('Confirm buy at position:', (x, y))
        self.click_on_point(x, y)

    def click_button(self, button_url):
        path = get_relative_path(button_url)
        button_center = self.safe_locate_center_button_on_game_window(path)
        if self.debug: save_debug_screenshot(path, center=button_center)
        if not button_center: raise Exception(f'Button {button_url} not found on the screen.')

        if self.debug: print('Found button at:', button_center)

        self.click_on_point(button_center.x, button_center.y)

    def click_on_point(self, x, y):
        rand_x = random.randint(-3, 3) + x
        rand_y = random.randint(-3, 3) + y

        pyautogui.moveTo(rand_x, rand_y, duration=self.mouse_sleep)
        if self.debug: print('Moving to:', (rand_x, rand_y))

        pyautogui.click(rand_x, rand_y, interval=self.mouse_sleep)
        if self.debug: print('Clicked at:', (rand_x, rand_y))

        time.sleep(random.uniform(self.mouse_sleep - 0.1, self.mouse_sleep + 0.1))

    def click_refresh(self):
        if self._stop_event.is_set():  # Check for stop at start
            return

        if self.debug: print('Clicking refresh button...')
        left, top, width, height = safe_get_window_param(self.game_window)
        x = left + width * 0.20
        y = top + height * 0.90

        self.click_on_point(x, y)

        if self._stop_event.is_set():  # Check for stop at start
            return

        if self.debug: time.sleep(1)
        self.click_confirm_refresh()

    def click_confirm_refresh(self):
        left, top, width, height = safe_get_window_param(self.game_window)

        x = left + width * 0.58
        y = top + height * 0.65

        if self._stop_event.is_set():  # Check for stop at start
            return

        self.click_on_point(x, y)

        time.sleep(random.uniform(self.screenshot_sleep - 0.1, self.screenshot_sleep + 0.1))

    def scroll_down(self):
        left, top, width, height = safe_get_window_param(self.game_window)

        start_x = left + width * 0.58
        start_y = top + height * 0.65
        end_y = start_y - height * 0.5

        pyautogui.moveTo(start_x, start_y, duration=0.2)
        pyautogui.dragTo(start_x, end_y, duration=0.5, button='left')
        time.sleep(max(0.3, self.screenshot_sleep) + 0.1)

    def scroll_up(self):
        left, top, width, height = safe_get_window_param(self.game_window)

        start_x = left + width * 0.58
        start_y = top + height * 0.65
        end_y = start_y + height * 0.5

        pyautogui.moveTo(start_x, start_y, duration=0.2)
        pyautogui.dragTo(start_x, end_y, duration=0.5, button='left')
        time.sleep(max(0.3, self.screenshot_sleep))

    def search_item(self, screenshot, item: ShopItem) -> pyautogui.Point | None:

        process_screenshot = cv2.GaussianBlur(screenshot, (3, 3), 0)
        process_item = cv2.GaussianBlur(item.search_image, (3, 3), 0)

        left, top, width, height = safe_get_window_param(self.game_window)

        result = cv2.matchTemplate(process_screenshot, process_item, cv2.TM_CCOEFF_NORMED)

        if self.debug_screenshot: self.debug_search(item, process_item, process_screenshot, result)

        loc = np.where(result >= 0.8)


        if loc[0].size > 0:
            x = left + width * 0.90
            y = top + loc[0][0] + height * 0.085
            pos = pyautogui.Point(x, y)
            return pos
        return None

        # if loc[0].size > 0:
        #     # Get the template match position
        #     match_y = loc[0][0]  # Y position in screenshot (window-relative)
        #     match_x = loc[1][0]  # X position in screenshot (window-relative)
        #
        #     template_h = item.search_image.shape[0]
        #
        #     # Calculate center of matched item
        #     item_center_y_window = match_y + template_h // 2
        #
        #     # Convert to screen coordinates
        #     item_center_y_screen = top + item_center_y_window
        #
        #     # Buy button X position (screen coordinates)
        #     buy_button_x = left + width * 0.90
        #
        #     if self.debug:
        #         print(f'Item matched at window coords: ({match_x}, {match_y})')
        #         print(f'Item center Y (window): {item_center_y_window}')
        #         print(f'Item center Y (screen): {item_center_y_screen}')
        #         print(f'Buy button will be at: ({buy_button_x}, {item_center_y_screen})')
        #
        #     pos = pyautogui.Point(buy_button_x, item_center_y_screen)
        #     return pos

    def debug_search(self, item: ShopItem, process_item: Mat | ndarray[Any, dtype[integer[Any] | floating[Any]]] | UMat,
                     process_screenshot: Mat | ndarray[Any, dtype[integer[Any] | floating[Any]]] | UMat,
                     result: Mat | ndarray[Any, dtype[integer[Any] | floating[Any]]]):
        # Save processed images and match result for debugging
        try:
            os.makedirs('debug_screenshots', exist_ok=True)
            timestamp = int(time.time() * 1000)
            base_name = f"debug_screenshots/{timestamp}_{os.path.basename(item.path).replace('.', '_')}"
            cv2.imwrite(base_name + "_screenshot.png", process_screenshot)
            cv2.imwrite(base_name + "_item.png", process_item)
            norm = cv2.normalize(result, None, 0, 255, cv2.NORM_MINMAX)
            heatmap = np.uint8(norm)
            heatmap_color = cv2.applyColorMap(heatmap, cv2.COLORMAP_JET)
            cv2.imwrite(base_name + "_result.png", heatmap_color)

        except Exception as e:
            if self.debug:
                print("Failed to save processed debug images:", e)


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
        self.ssr = SecretShopRefresh(title_name=self.app_config.app_title, terminate_callback=self.refresh_complete,
                                     debug=self.app_config.DEBUG)

        self.ssr.settings_window = self.settings_window

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
    RefresherGUI()