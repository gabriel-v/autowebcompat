import glob
import logging
import json
import multiprocessing
import threading
import os
import random
import time
from urllib.parse import urlparse

# from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from PIL import Image
from lxml import etree
from selenium import webdriver
from selenium.common.exceptions import NoAlertPresentException
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import NoSuchWindowException
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import WebDriverException

from autowebcompat import utils

#try:
#    # https://github.com/abseil/abseil-py/issues/99
#    import absl.logging
#    logging.root.removeHandler(absl.logging._absl_handler)
#    absl.logging._warn_preinit_stderr = False
#except Exception as e:
#    print("Failed to fix absl logging bug", e)
#    pass

BROWSER_LANG = 'it'

WINDOW_HEIGHT = 732
WINDOW_WIDTH = 412

FIREFOX_WIDTH_OFFSET = 0
FIREFOX_HEIGHT_OFFSET = 54

BROWSER_TIMEOUT = 30
MAX_PROC = 5
MAX_INTERACTION_DEPTH = 7

MAX_SCROLL = 5000

ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.realpath(os.getenv('DATA_DIR', '/local/data'))
BUGS_PATH = os.path.realpath(os.getenv('BUGS_PATH', os.path.join(ROOT, 'webcompatdata-bzlike.json')))

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(processName) %(threadName) %(levelname)8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('collect')
log.setLevel(logging.DEBUG)
log.info('scraping bugs from %s into %s', BUGS_PATH, DATA_DIR)

utils.mkdir(DATA_DIR)
bugs = utils.get_bugs(BUGS_PATH)

log.info('read %s bugs', len(bugs))

with open(os.path.join(ROOT, 'get_xpath.js'), 'r') as f:
    get_xpath_script = f.read()


def set_timeouts(driver):
    driver.set_script_timeout(BROWSER_TIMEOUT)
    driver.set_page_load_timeout(BROWSER_TIMEOUT)
    driver.implicitly_wait(BROWSER_TIMEOUT)


def wait_loaded(driver):
    log.info('waiting for driver...')
    try:
        driver.execute_async_script("""
          let done = arguments[0];

          window.onload = done;
          if (document.readyState === 'complete') {
            done();
          }
        """)
    except Exception as e:  # noqa: E722
        log.exception(e)
        log.warning('Continuing (1)...')

    # We hope the page is fully loaded in 7 seconds.
    time.sleep(7)

    try:
        driver.execute_async_script("""
          window.requestIdleCallback(arguments[0], {
            timeout: 60000
          });
        """)
    except Exception as e:  # noqa: E722
        log.exception(e)
        log.warning('Continuing (2)...')


def close_all_windows_except_first(driver):
    windows = driver.window_handles

    log.debug('closing %d windows', len(windows) - 1)
    for window in windows[1:]:
        driver.switch_to.window(window)
        driver.close()

    while True:
        try:
            alert = driver.switch_to.alert()
            alert.dismiss()
            log.debug('alert dismissed')
        except (NoAlertPresentException, NoSuchWindowException):
            break

    driver.switch_to.window(windows[0])


def get_element_properties(driver, child):
    child_properties = driver.execute_script("""
      let elem_properties = {
        tag: '',
        attributes: {},
      };

      for (let i = 0; i < arguments[0].attributes.length; i++) {
        elem_properties.attributes[arguments[0].attributes[i].name] = arguments[0].attributes[i].value;
      }
      elem_properties.tag = arguments[0].tagName;

      return elem_properties;
    """, child)

    return child_properties


def get_elements_with_properties(driver, elem_properties, children):
    elems_with_same_properties = []
    for child in children:
        child_properties = get_element_properties(driver, child)
        if child_properties == elem_properties:
            elems_with_same_properties.append(child)
    return elems_with_same_properties


def was_visited(current_path, visited_paths, elem_properties):
    current_path_elements = [element for element, _, _ in current_path]
    current_path_elements.append(elem_properties)
    if current_path_elements in visited_paths:
        return True
    else:
        visited_paths.append(current_path_elements[:])
        return False


def do_something(driver, visited_paths, current_path, elem_properties=None, xpath=None):
    log.info('doing something with visited_paths=%s xpath=%s current_path=%s', visited_paths, xpath, current_path)
    elem = None

    body = driver.find_elements_by_tag_name('body')
    assert len(body) == 1
    body = body[0]

    buttons = body.find_elements_by_tag_name('button')
    links = body.find_elements_by_tag_name('a')
    inputs = body.find_elements_by_tag_name('input')
    selects = body.find_elements_by_tag_name('select')
    children = buttons + links + inputs + selects

    if elem_properties is None and xpath is None:
        random.shuffle(children)
        children_to_ignore = []  # list of elements with same properties to ignore

        for child in children:
            if child in children_to_ignore:
                continue

            # Get all the properties of the child.
            elem_properties = get_element_properties(driver, child)

            # If the element is not displayed or is disabled, the user can't interact with it. Skip
            # non-displayed/disabled elements, since we're trying to mimic a real user.
            if not child.is_displayed() or not child.is_enabled():
                continue

            if was_visited(current_path, visited_paths, elem_properties):
                continue

            elem = child

            # We mark the current path as visited
            elems = get_elements_with_properties(driver, elem_properties, children)
            if len(elems) == 1:
                elem = child
                xpath = driver.execute_script(get_xpath_script, elem)
                break
            else:
                children_to_ignore.extend(elems)
    else:
        if 'id' in elem_properties['attributes'].keys():
            elem_id = elem_properties['attributes']['id']
            elem = driver.find_element_by_id(elem_id)
            if xpath is None:
                xpath = driver.execute_script(get_xpath_script, elem)
        elif xpath is not None:
            try:
                elem = driver.find_element_by_xpath(xpath)
            except NoSuchElementException:
                elems = get_elements_with_properties(driver, elem_properties, children)
                assert len(elems) == 1
                elem = elems[0]
                xpath = driver.execute_script(get_xpath_script, elem)
        else:
            elems = get_elements_with_properties(driver, elem_properties, children)
            assert len(elems) == 1
            elem = elems[0]
            xpath = driver.execute_script(get_xpath_script, elem)

    if elem is None:
        log.warning('no element found in do_something')
        return None

    driver.execute_script('arguments[0].scrollIntoView();', elem)

    if elem.tag_name in ['button', 'a']:
        elem.click()
    elif elem.tag_name == 'input':
        input_type = elem.get_attribute('type')
        if input_type == 'url':
            elem.send_keys('http://www.mozilla.org/')
        elif input_type == 'text':
            elem.send_keys('marco')
        elif input_type == 'email':
            elem.send_keys(f'prova@email.{BROWSER_LANG}')
        elif input_type == 'password':
            elem.send_keys('aMildlyComplexPasswordIn2017')
        elif input_type == 'checkbox':
            elem.click()
        elif input_type == 'number':
            elem.send_keys('3')
        elif input_type == 'radio':
            elem.click()
        elif input_type == 'search':
            elem.clear()
            elem.send_keys('quick search')
        elif input_type == 'submit':
            elem.click()
        elif input_type == 'color':
            driver.execute_script("arguments[0].value = '#ff0000'", elem)
        else:
            raise Exception('Unsupported input type: %s' % input_type)
    elif elem.tag_name == 'select':
        for option in elem.find_elements_by_tag_name('option'):
            if option.text != '':
                option.click()
                break

    close_all_windows_except_first(driver)

    return elem_properties, xpath


def screenshot(driver, bug_id, browser, seq_no):
    log.info('taking screenshot for bug %s on %s seq %s', bug_id, browser, seq_no)
    page_height = driver.execute_script('return document.body.scrollHeight;')
    page_width = driver.execute_script('return document.body.scrollWidth;')
    height = 0
    while height < min(page_height, MAX_SCROLL):
        width = 0
        while width < min(page_width, MAX_SCROLL):
            file_name = utils.create_file_name(bug_id=bug_id, browser=browser, width=str(width), height=str(height), seq_no=seq_no) + '.png'
            file_name = os.path.join(DATA_DIR, file_name)
            driver.execute_script('window.scrollTo(arguments[0], arguments[1]);', width, height)
            driver.get_screenshot_as_file(file_name)
            image = Image.open(file_name)
            assert image.size == (WINDOW_WIDTH, WINDOW_HEIGHT), f'size mismatch: got {image.size}, expected {(WINDOW_WIDTH, WINDOW_HEIGHT)}'
            log.info('saving %s', file_name)
            image.save(file_name)
            width += WINDOW_WIDTH
        height += WINDOW_HEIGHT


def get_domtree(driver, bug_id, browser, seq_no):
    log.info('getting domtree for bug %s on %s seq %s', bug_id, browser, seq_no)
    file_name = 'dom_' + utils.create_file_name(bug_id=bug_id, browser=browser, seq_no=seq_no) + '.txt'
    file_name = os.path.join(DATA_DIR, file_name)
    log.info('saving %s', file_name)
    with open(file_name, 'w', encoding='utf-8') as f:
        f.write(driver.execute_script('return document.documentElement.outerHTML'))


def get_coordinates(driver, bug_id, browser, seq_no):
    log.info('getting coordinates for bug %s on %s seq %s', bug_id, browser, seq_no)
    dom_tree = etree.HTML(driver.execute_script('return document.documentElement.outerHTML'))
    dom_element_tree = etree.ElementTree(dom_tree)
    loc_dict = {}
    dom_tree_elements = [elem for elem in dom_tree.iter(tag=etree.Element)]
    web_elements = driver.find_elements_by_css_selector('*')
    dom_xpaths = []

    log.debug('iterating %s dom_tree_elements', len(dom_tree_elements))
    for element in dom_tree_elements:
        dom_xpaths.append(dom_element_tree.getpath(element))

    log.debug('iterating %s web_elements', len(web_elements))
    for element in web_elements:
        xpath = driver.execute_script(get_xpath_script, element)

        if xpath in dom_xpaths:
            loc_dict[xpath] = element.size
            loc_dict[xpath].update(element.location)
            dom_xpaths.remove(xpath)

    log.debug('iterating %s dom_xpaths', len(dom_xpaths))
    for xpath in dom_xpaths:
        try:
            element = driver.find_element_by_xpath(xpath)
        except NoSuchElementException:
            log.debug('no such element')
            continue
        loc_dict[xpath] = element.size
        loc_dict[xpath].update(element.location)

    file_name = 'loc_' + utils.create_file_name(bug_id=bug_id, browser=browser, seq_no=seq_no) + '.txt'
    file_name = os.path.join(DATA_DIR, file_name)
    log.info('saving %s', file_name)
    with open(file_name, 'w') as f:
        json.dump(loc_dict, f)


def get_screenshot_and_domtree(driver, bug, browser, seq_no=None):
    try:
        driver.get(bug['url'])
    except TimeoutException as e:
        log.exception(e)
        log.warning('Continuing...')
    wait_loaded(driver)

    bug_id = str(bug['id'])
    screenshot(driver, bug_id, browser, seq_no)
    get_domtree(driver, bug_id, browser, seq_no)
    get_coordinates(driver, bug_id, browser, seq_no)


def count_lines(bug_id):
    try:
        with open(os.path.join(DATA_DIR, '%d.txt' % bug_id)) as f:
            return sum(1 for line in f)
    except IOError:
        return 0


# We restart from the start and follow the saved sequence
def jump_back(current_path, firefox_driver, chrome_driver, visited_paths, bug):
    log.debug('jump back in bug %s: current_path=%', bug['id'], current_path)
    firefox_driver.get(bug['url'])
    chrome_driver.get(bug['url'])
    for (elem_properties, firefox_xpath, chrome_xpath) in current_path:
        do_something(firefox_driver, visited_paths, current_path, elem_properties, firefox_xpath)
        do_something(chrome_driver, visited_paths, current_path, elem_properties, chrome_xpath)


def run_in_parallel(func, args1, args2):
    t1 = threading.Thread(
        target=func,
        args=args1,
    )
    t2 = threading.Thread(
        target=func,
        args=args2,
    )
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def run_test_both(bug, firefox_driver, chrome_driver):
    log.info('Testing bug %s on %s', bug['id'], urlparse(bug['url']).netloc)
    run_in_parallel(
        func=get_screenshot_and_domtree,
        args1=(chrome_driver, bug, 'chrome'),
        args2=(firefox_driver, bug, 'firefox'),
    )
    log.info('first sequence done')

    visited_paths = []
    current_path = []
    while True:
        elem_properties, firefox_xpath = do_something(firefox_driver, visited_paths, current_path)
        log.info('  - Using %s' % elem_properties)
        if elem_properties is None:
            if not current_path:
                log.info('============= Completed (%d) =============' % bug['id'])
                break
            current_path.pop()
            jump_back(current_path, firefox_driver, chrome_driver, visited_paths, bug)
            continue

        _, chrome_xpath = do_something(chrome_driver, visited_paths, current_path, elem_properties)
        current_path.append((elem_properties, firefox_xpath, chrome_xpath))

        with open(os.path.join(DATA_DIR, '%d.txt' % bug['id']), 'a+') as f:
            line_number = count_lines(bug['id'])
            f.seek(2, 0)
            for element, _, _ in current_path:
                f.write(json.dumps(element) + '\n')
            f.write('\n')

        run_in_parallel(
            func=get_screenshot_and_domtree,
            args1=(firefox_driver, bug, 'firefox', str(line_number)),
            args2=(chrome_driver, bug, 'chrome', str(line_number)),
        )
        log.info('sequence done')

        if len(current_path) == MAX_INTERACTION_DEPTH:
            current_path.pop()
            jump_back(current_path, firefox_driver, chrome_driver, visited_paths, bug)


def run_test(firefox_driver, chrome_driver, bug):
    log.info("Running test for bug %s", bug['id'])
    firefox_driver.set_window_size(WINDOW_WIDTH + FIREFOX_WIDTH_OFFSET,
                                   WINDOW_HEIGHT + FIREFOX_HEIGHT_OFFSET)
    set_timeouts(firefox_driver)
    set_timeouts(chrome_driver)

    try:
        # We attempt to regenerate everything when either
        # a) we haven't generated the main screenshot for Firefox or Chrome, or
        # b) we haven't generated any item of the sequence for Firefox, or
        # c) there are items in the Firefox sequence that we haven't generated for Chrome.
        lines_written = count_lines(bug['id'])
        number_of_ff_scr = len(glob.glob(os.path.join(DATA_DIR, '%d_*_firefox.png' % bug['id'])))
        number_of_ch_scr = len(glob.glob(os.path.join(DATA_DIR, '%d_*_chrome.png' % bug['id'])))
        if not os.path.exists(os.path.join(DATA_DIR, '%d_firefox.png' % bug['id'])) or \
           not os.path.exists(os.path.join(DATA_DIR, '%d_chrome.png' % bug['id'])) or \
           lines_written == 0 or \
           number_of_ff_scr != number_of_ch_scr:
            for f in glob.iglob(os.path.join(DATA_DIR, '%d*' % bug['id'])):
                log.warning('removing %s', f)
                os.remove(f)
            run_test_both(bug, firefox_driver, chrome_driver)

    except Exception as e:  # noqa: E722
        log.exception(e)


def main(bugs):
    HUB_HOST = os.environ['HUB_HOST']
    HUB_PORT = os.environ['HUB_PORT']
    HUB_URL = 'http://%s:%s/wd/hub' % (HUB_HOST, HUB_PORT)
    log.info('%s main: connecting to %s', __file__, HUB_URL)

    firefox_options = webdriver.FirefoxOptions()
    firefox_options.headless = True
    firefox_options.profile = webdriver.FirefoxProfile(os.path.join(ROOT, 'firefox_profile'))
    firefox_options.set_preference('general.useragent.override', 'Mozilla/5.0 (Android 6.0.1; Mobile; rv:54.0) Gecko/54.0 Firefox/54.0')
    firefox_options.set_preference('intl.accept_languages', BROWSER_LANG)
    firefox_options.set_preference('media.volume_scale', '0.0')

    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--hide-scrollbars')
    chrome_options.add_argument('--whitelisted-ips')
    chrome_options.add_argument(f'--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5 Build/M4B30Z) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.83 Mobile Safari/537.36')
    chrome_options.add_argument(f'--lang={BROWSER_LANG}')
    chrome_options.add_argument('--mute-audio')

    ff_cap = firefox_options.to_capabilities()
    ch_cap = chrome_options.to_capabilities()

    for bug in bugs:
        log.info('requesting browsers for bug %s', bug['id'])
        t0 = time.time()
        with webdriver.Remote(command_executor=HUB_URL, desired_capabilities=ff_cap, keep_alive=True) as firefox_driver, \
                webdriver.Remote(command_executor=HUB_URL, desired_capabilities=ch_cap, keep_alive=True) as chrome_driver:
            log.info('got browsers for bug %s after %s sec', bug['id'], time.time() - t0)
            run_test(firefox_driver, chrome_driver, bug)


if __name__ == '__main__':
    log.info('reshuffling bugs...')
    random.shuffle(bugs)
    if MAX_PROC == 1:
        main(bugs)
    else:
        log.info('splitting up...')
        with multiprocessing.Pool(MAX_PROC) as pool:
            pool.map(main, [bugs[i::MAX_PROC] for i in range(MAX_PROC)])
