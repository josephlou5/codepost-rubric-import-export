"""
screenshot.py
Screenshots a codePost comment.
"""

# ===========================================================================

import asyncio
import os
import re
import time
from typing import (
    Any,
    Sequence, List, Tuple, Dict,
    Optional, Union,
)

import codepost
import comma
# noinspection PyUnresolvedReferences
# ref: https://github.com/miyakogi/pyppeteer/issues/219#issuecomment-563077061
import pyppdf.patch_pyppeteer  # needed to avoid chromium errors
import pyppeteer.browser
import pyppeteer.errors
import pyppeteer.page
from loguru import logger
from PIL import Image, ImageFont, ImageDraw, ImageOps
from pyppeteer import launch

from shared import *
from shared_codepost import *
from shared_output import *

# ===========================================================================

# types

Font = Union[ImageFont.ImageFont, ImageFont.FreeTypeFont]

# outputs

SCREENSHOT_FOLDER = 'screenshots'
FILES = {
    'screenshot': '{}_{}.png',  # submission id, comment id
    'rubric comment': '{}_{}_{}.png',  # submission id, comment id, comment name
}

# globals

LINK_PATTERN = re.compile(r'https://codepost.io/code/(\d+)/\?comment=(\d+)')

# maps rubric comment id -> (comment name, category name)
RUBRIC_COMMENTS: Dict[int, Tuple[str, str]] = dict()
# maps rubric cateogry id -> rubric category name
RUBRIC_CATEGORIES: Dict[int, RubricComment] = dict()

FONT_SIZE = 14
ONE_LINE_FONT_SIZE = 10
TITLE_FONTS = ['Roboto-Bold', 'FiraSans-Bold', 'SF-Pro-Text-Bold', 'Arial Bold']
SANS_FONTS = ['Roboto-Regular', 'FiraSans-Regular', 'SF-Pro-Text-Regular', 'Arial']
MONO_FONTS = ['FiraCode-VariableFont_wght', 'SF-Mono-Regular', 'Courier']

# constants

LOGIN_URL = 'https://codepost.io/login'
JWT_KEY = 'need new jwt key'
WHITE: Color = (255, 255, 255)
CODEPOST_GREEN: Color = (87, 177, 130)


# ===========================================================================

def get_font(fontnames: Sequence[str],
             size: int = 10,
             default: Font = ImageFont.load_default(),
             log: bool = False
             ) -> Font:
    """Gets a font.

    Args:
        fontnames (Sequence[str]): The font names to try, in order.
        size (int): The font size.
            Default is 10.
        default (Font): The default font if none are found.
            Default is `ImageFont.load_default()`.
        log (bool): Whether to show log messages.
            Default is False.

    Returns:
        Font: The font.
    """

    for fontname in fontnames:
        try:
            return ImageFont.truetype(fontname, size=size)
        except OSError:
            if log: logger.warning('Font "{}" not found', fontname)
    return default


# ===========================================================================

def extract_link(link: str,
                 log: bool = False
                 ) -> Union[Tuple[None, None], Tuple[int, int]]:
    """Extracts the submission id and comment id from a link.

    Args:
        link (str): The link.
        log (bool): Whether to show log messages.
            Default is False.

    Returns:
        Union[Tuple[None, None], Tuple[int, int]]: The submission id and comment id.
            Returns None, None if the link is invalid.
    """

    match = LINK_PATTERN.match(link)
    if match is None:
        if log: logger.warning('Invalid link: "{}"', link)
        return None, None
    s_id, c_id = map(int, match.groups())
    return s_id, c_id


def read_comments_from_file(file: str,
                            log: bool = False
                            ) -> List[Tuple[int, int]]:
    """Reads comments from a file.

    Args:
        file (str): The file.
        log (bool): Whether to show log messages.
            Default is False.

    Returns:
        List[Tuple[int, int]]: The submission ids and comment ids.
    """

    # validate file
    ext = validate_file(file, log=log)
    if ext is None:
        return list()

    if log: logger.info('Reading links from file')

    comments: List[Tuple[int, int]] = list()

    # txt file: one link per line
    if ext == '.txt':
        with open(file, 'r') as f:
            for line in f.read().split('\n'):
                s_id, c_id = extract_link(line.strip(), log=log)
                if s_id is None or c_id is None:
                    continue
                comments.append((s_id, c_id))
    # csv file: "link" column or "submission_id" and "comment_id" columns
    elif ext == '.csv':
        data = comma.load(file, force_header=True)
        LINK_KEY = 'link'
        S_ID_KEY = 'submission_id'
        C_ID_KEY = 'comment_id'
        if LINK_KEY in data.header:
            for link in data[LINK_KEY]:
                s_id, c_id = extract_link(link, log=log)
                if s_id is None or c_id is None:
                    continue
                comments.append((s_id, c_id))
        elif S_ID_KEY not in data.header or C_ID_KEY not in data.header:
            if log: logger.warning('File "{}" does not have proper columns', file)
        else:
            for s_id_val, c_id_val in zip(data[S_ID_KEY], data[C_ID_KEY]):
                s_id = s_id_val.strip()
                c_id = c_id_val.strip()
                if s_id.isdigit() and c_id.isdigit():
                    comments.append((int(s_id), int(c_id)))

    if log: logger.debug('Found {} comments', len(comments))

    return comments


# ===========================================================================

class CodePostPage:
    """CodePostPage class: Represents a Pyppeteer page linked to a submission.

    Constructors:
        CodePostPage.create(browser, submission_id, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT)
            Initializes a CodePostPage.

    Constants:
        DEFAULT_WIDTH (int): The default width.
        DEFAULT_HEIGHT (int): The default height.

    Properties:
        width (int): The width.
        height (int): The height.
        size (Tuple[int, int]): The width and height.

    Methods (all coroutines):
        set_width(width)
            Sets the width.

        set_height(height)
            Sets the height.

        set_size(width, height)
            Sets the width and height.

        open_submission(timeout=60)
            Opens the submission.

        evaluate(*args, **kwargs)
            Proxy for `pyppeteer.page.Page.evaluate()`.

        hide_grade()
            Hides the grade element.

        select_file(file_index)
            Selects a file.

        collapse_sections(ignore=None)
            Collapses the sections on the left.

        align_comment(comment)
            Aligns a comment.

        hide_voting(comment_id)
            Hides the upvote/downvote buttons.

        hide_elements(ids=None, classes=None)
            Hides elements on the page.

        cover_elements(ids=None, classes=None)
            Covers elements on the page.

        reset_column_width(code=False, comment=False)
            Resets the column width of the code and comment panels.

        set_column_width(code=None, comment=None, slider=False)
            Sets the column width of the code and comment panels.

        screenshot(path, x=None, y=None, width=None, height=None)
            Takes and saves a screenshot.
    """

    # ==================================================

    # constants
    DEFAULT_WIDTH: int = 1450
    DEFAULT_HEIGHT: int = 900

    # ==================================================

    # constructors

    def __init__(self, browser, page, submission_id, explanation, width, height):
        """Initializes a Page. Not meant for public calls."""
        self._browser: pyppeteer.browser.Browser = browser
        self._page: pyppeteer.page.Page = page
        self._s_id: int = submission_id
        self._explanation: bool = explanation
        self._width: int = width
        self._height: int = height

        self._selected_file: int = 0

    @classmethod
    async def create(cls,
                     browser: pyppeteer.browser.Browser,
                     submission_id: int,
                     explanation: bool = False,
                     width: int = DEFAULT_WIDTH,
                     height: int = DEFAULT_HEIGHT
                     ) -> 'CodePostPage':
        """Initializes a CodePostPage.

        Args:
            browser (pyppeteer.browser.Browser): The browser.
            submission_id (int): The submission id.
            explanation (bool): Whether to show the comment explanation.
                Default is False.
            width (int): The width of the page.
                Default is `DEFAULT_WIDTH`.
            height (int): The height of the page.
                Default is `DEFAULT_HEIGHT`.
        """

        page = cls(browser, await browser.newPage(), submission_id, explanation, width, height)
        await page._update_size()
        return page

    # ==================================================

    # private methods

    async def _update_size(self):
        await self._page.setViewport({'width': self._width, 'height': self._height})

    # ==================================================

    # properties

    @property
    def width(self) -> int:
        """Gets the width."""
        return self._width

    async def set_width(self, width: int):
        """Sets the width."""
        self._width = width
        await self._update_size()

    @property
    def height(self) -> int:
        """Gets the height."""
        return self._height

    async def set_height(self, height: int):
        """Sets the height."""
        self._height = height
        await self._update_size()

    @property
    def size(self) -> Tuple[int, int]:
        """Gets the width and height."""
        return self._width, self._height

    async def set_size(self, width: int, height: int):
        """Sets the width and height."""
        self._width, self._height = width, height
        await self._update_size()

    # ==================================================

    # public methods

    async def open_submission(self,
                              timeout: int = 60
                              ) -> bool:
        """Opens the submission.

        Args:
            timeout (int): The timeout limit for the page to load, in seconds.
                A timeout limit of 0 means no timeout.
                Default is 60 ms.

        Returns:
            bool: Whether the page successfully loaded.
        """

        link = f'https://codepost.io/code/{self._s_id}'

        # use student view to see explanation
        if self._explanation:
            link += '?student=1'

        # load page and wait to make sure everything is rendered
        waiting = [
            'load',  # load event
            'domcontentloaded',  # DOMContentLoaded event
            'networkidle0',  # no more than 0 network connections for at least 500 ms
            'networkidle2',  # no more than 2 network connections for at least 500 ms
        ]

        try:
            await self._page.goto(link, timeout=timeout * 1000, waitUntil=waiting)
        except pyppeteer.errors.TimeoutError:
            return False

        return True

    async def evaluate(self, *args, **kwargs) -> Any:
        """Proxy for `pyppeteer.page.Page.evaluate()`."""
        return await self._page.evaluate(*args, **kwargs)

    async def hide_grade(self):
        """Hides the grade element."""
        await self._page.evaluate(
            '''() => {
                const header = document.getElementsByClassName("layout--standard-console__header")[0];
                const gradeDiv = header.firstElementChild.children[1];
                gradeDiv.style.display = "none";
            }'''
        )

    async def select_file(self, file_index: int):
        """Selects a file.

        Args:
            file_index (int): The index of the file to select.
        """

        if file_index == self._selected_file:
            return

        async def _click_file():
            # using click() scrolls the files page just in case it needs to be scrolled
            files = await self._page.querySelectorAll('#file-menu li')
            await files[file_index].click()

        async def _keyboard_shortcut():
            await self._page.keyboard.down('Meta')
            await self._page.keyboard.press(str(file_index + 1))
            await self._page.keyboard.up('Meta')

        # easiest method
        await _keyboard_shortcut()
        self._selected_file = file_index

    async def collapse_sections(self, ignore: Union[int, Sequence[int]] = None):
        """Collapses the sections on the left.

        Args:
            ignore (Union[int, Sequence[int]]): Which sections to ignore.
                Default is None.
                0 - Submission Info
                1 - Tests
                2 - Files
                3 - Rubric
        """

        if ignore is None:
            ignore = list()
        elif type(ignore) is int:
            ignore = [ignore]

        section_arrows = await self._page.querySelectorAll('.ant-collapse-arrow')
        for i, arrow in enumerate(section_arrows):
            # skip sections
            if i in ignore: continue
            # collapse section by clicking on its arrow
            await arrow.click()

    async def align_comment(self, comment: Comment):
        """Aligns a comment.

        Args:
            comment (Comment): The comment.
        """

        highlight = await self._page.querySelector(f'#line-{comment.startLine}-{comment.id}')
        await self._page.keyboard.down('Meta')
        await highlight.click()
        await self._page.keyboard.up('Meta')

    async def hide_voting(self, comment: Comment):
        """Hides the upvote/downvote buttons.

        Args:
            comment (Comment): The comment.
        """

        if not self._explanation or comment.rubricComment is None: return
        await self._page.evaluate(
            '''(commentID) => {
                const comment = document.getElementById("comment-" + commentID);
                comment.children.forEach((x) => {
                    if (x.classList.contains("ant-btn-group")) {
                        x.style.display = "none";
                    }
                });
            }''',
            comment.id
        )

    async def hide_elements(self, ids: Sequence[str] = None, classes: Sequence[str] = None):
        """Hides elements on the page.

        Args:
            ids (Sequence[str]): The element ids to hide.
                Default is None.
            classes (Sequence[str]): The element classes to hide.
                Default is None.
        """

        if ids is None and classes is None:
            return

        HIDE_IDS = '''
        ids.forEach((i) => {
            document.getElementById(i).style.display = "none";
        });'''

        HIDE_CLASSES = '''
        classes.forEach((c) => {
            document.getElementsByClassName(c).forEach((x) => {
                x.style.display = "none";
            });
        });'''

        if ids is not None and classes is not None:
            await self._page.evaluate(
                '(ids, classes) => {' + HIDE_IDS + HIDE_CLASSES + '}',
                ids, classes
            )
        elif ids is not None:
            await self._page.evaluate(
                '(ids) => {' + HIDE_IDS + '}',
                ids
            )
        elif classes is not None:
            await self._page.evaluate(
                '(classes) => {' + HIDE_CLASSES + '}',
                classes
            )

    async def cover_elements(self, ids: Sequence[str] = None, classes: Sequence[str] = None):
        """Covers elements on the page.

        Args:
            ids (Sequence[str]): The element ids to cover.
                Default is None.
            classes (Sequence[str]): The element classes to cover.
                Default is None.
        """

        if ids is None and classes is None:
            return

        COVER_IDS = '''
        ids.forEach((i) => {
            document.getElementById(i).style.visibility = "hidden";
        });'''

        COVER_CLASSES = '''
        classes.forEach((c) => {
            document.getElementsByClassName(c).forEach((x) => {
                x.style.visibility = "hidden";
            });
        });'''

        if ids is not None and classes is not None:
            await self._page.evaluate(
                '(ids, classes) => {' + COVER_IDS + COVER_CLASSES + '}',
                ids, classes
            )
        elif ids is not None:
            await self._page.evaluate(
                '(ids) => {' + COVER_IDS + '}',
                ids
            )
        elif classes is not None:
            await self._page.evaluate(
                '(classes) => {' + COVER_CLASSES + '}',
                classes
            )

    async def reset_column_width(self, code: bool = False, comment: bool = False):
        """Resets the column width of the code and comment panels.

        Args:
            code (bool): Whether to reset the code panel.
                Default is False.
            comment (bool): Whether to reset the comment panel.
                Default is False.
        """

        # default 728 px
        if code:
            await self._page.evaluate(
                'document.getElementById("code-container").style.width = "728px";',
                force_expr=True
            )
        # default 360 px
        if comment:
            await self._page.evaluate(
                'document.getElementById("code-panel--comments").style.width = "360px";',
                force_expr=True
            )

    async def set_column_width(self, code: int = None, comment: int = None, slider: bool = False):
        """Sets the column width of the code and comment panels.

        Args:
            code (int): The width of the code panel.
                Default is None.
            comment (int): The width of the comment panel.
                Default is None.
            slider (bool): Whether to update the position of the slider.
                Default is False.
        """

        if code is not None:
            await self._page.evaluate(
                f'document.getElementById("code-container").style.width = "{code}px";',
                force_expr=True
            )

            if slider:
                slider_max = await self._page.evaluate(
                    '''parseFloat(
                        document.getElementsByClassName("rc-slider-handle-2")[0].getAttribute("aria-valuemax")
                    );''',
                    force_expr=True
                )
                slider_per = code / slider_max * 100
                await self._page.evaluate(
                    '''(sliderPer) => {
                        document.getElementsByClassName("rc-slider-track-1")[0].style.width = sliderPer + "%";
                        document.getElementsByClassName("rc-slider-handle-2")[0].style.left = sliderPer + "%";
                    }''',
                    slider_per
                )

        if comment is not None:
            await self._page.evaluate(
                f'document.getElementById("code-panel--comments").style.width = "{comment}px";',
                force_expr=True
            )

    async def screenshot(self,
                         path: str,
                         x: int = 0,
                         y: int = 0,
                         width: int = None,
                         height: int = None
                         ):
        """Takes and saves a screenshot.

        Args:
            path (str): The path to save the screenshot.
            x (int): The x-value of the top-left of the screenshot.
                Default is 0.
            y (int): The y-value of the top-left of the screenshot.
                Default is 0.
            width (int): The width of the screenshot.
                Default is `self.width`.
            height (int): The height of the screenshot.
                Default is `self.height`.
        """

        if width is None: width = self._width
        if height is None: height = self._height

        clip = {
            'x': x,
            'y': y,
            'width': width,
            'height': height,
        }

        await self._page.screenshot(path=path, clip=clip)


# ===========================================================================

async def take_screenshot(submission_id: int,
                          comment_id: int,
                          filepath: str,
                          page: CodePostPage,
                          strs: Sequence[str],
                          fit_to_comment: bool = False,
                          one_line: bool = False,
                          corner: bool = False,
                          adjust: bool = False,
                          log: bool = False
                          ):
    """Takes the screenshot and adds the metadata tattoo.

    Args:
        submission_id (int): The submission id.
        comment_id (int): The comment id.
        filepath (str): The path of the screenshot.
        page (CodePostPage): The page.
        strs (Sequence[str]): The tattoo information.
        fit_to_comment (bool): Whether to fit the screenshot to the comment.
            Default is False.
        one_line (bool): Whether to make the tattoo one line.
            Default is False.
        corner (bool): Whether to optimize the corner of the tattoo.
            Default is False.
        adjust (bool): Whether to adjust the tattoo to not overlap the comment.
            Default is False.
        log (bool): Whether to show log messages.
            Default is False.
    """

    # if fit to comment, make the tattoo one line to save image space
    if fit_to_comment: one_line = True

    # getting cropping area for screenshot
    if log: logger.debug('{}:{}: Getting cropping area', submission_id, comment_id)

    margins = await page.evaluate(
        '''() => {
            const codePanel = document.getElementsByClassName("code-panel--code")[0];
            const style = codePanel.currentStyle || window.getComputedStyle(codePanel);
            return {
                left: parseFloat(style.marginLeft),
                right: parseFloat(style.marginRight),
            };
        }'''
    )
    side_padding = margins['left']
    middle_padding = margins['right']

    # actual code width should be same as `code_width`
    # actual comment width should be `comment_width - 10` because there's a 10px padding on the right
    actual_width = await page.evaluate(
        '''(commentID) => {
            return {
                code: document.getElementsByClassName("code-panel--code")[0].offsetWidth,
                comment: document.getElementById("comment-" + commentID).offsetWidth,
            };
        }''',
        comment_id
    )

    pic_width = side_padding + actual_width['code'] + middle_padding + actual_width['comment'] + side_padding
    if pic_width > page.width:
        # resize page to accommodate screenshot
        await page.set_width(pic_width)

    heights = await page.evaluate(
        '''(commentID) => {
            const slider = document.getElementById("code-panel").firstElementChild;
            const sliderStyle = slider.currentStyle || window.getComputedStyle(slider);
            const comment = document.getElementById("comment-" + commentID);
            const commentStyle = comment.currentStyle || window.getComputedStyle(comment);
            return {
                slider: slider.offsetHeight + parseFloat(sliderStyle.marginTop) + parseFloat(sliderStyle.marginBottom),
                code: document.getElementById("code-container").offsetHeight,
                comment: comment.offsetHeight,
                top: parseFloat(commentStyle.top), 
            };
        }''',
        comment_id
    )

    COMMENT_PADDING = 20
    BOX_PADDING = 5
    BOX_WIDTH = 0
    X_PADDING = 15
    Y_PADDING = 15
    COL_SPACE = 5
    LINE_SPACE = 5

    TITLES = ['Assignment:', 'Submission:', 'Comment ID:', 'File:', 'Category:', 'Comment:']

    # tattoo rectangle and texts
    if one_line:

        # getting font
        mono_font = get_font(MONO_FONTS, size=ONE_LINE_FONT_SIZE)

        text = ' '.join(strs)
        text_width, text_height = mono_font.getsize(text)

        rect_width = BOX_WIDTH + BOX_PADDING + text_width + BOX_PADDING + BOX_WIDTH
        rect_height = BOX_WIDTH + BOX_PADDING + text_height + BOX_PADDING + BOX_WIDTH

        x = y = BOX_WIDTH + BOX_PADDING
        texts = [(x, y, text, {'fill': WHITE, 'font': mono_font})]

    else:

        # getting fonts
        title_font = get_font(TITLE_FONTS, size=FONT_SIZE)
        sans_font = get_font(SANS_FONTS, size=FONT_SIZE)
        mono_font = get_font(MONO_FONTS, size=FONT_SIZE)

        fonts: List[Font] = [sans_font, mono_font, mono_font, mono_font, mono_font, mono_font]

        title_width = 0
        max_height = 0
        for _, title in zip(strs, TITLES):
            w, h = title_font.getsize(title)
            if w > title_width:
                title_width = w
            if h > max_height:
                max_height = h
        x1 = BOX_WIDTH + BOX_PADDING
        x2 = x1 + title_width + COL_SPACE

        info_width = 0
        for s, font in zip(strs, fonts):
            w, h = font.getsize(s)
            if w > info_width:
                info_width = w
            if h > max_height:
                max_height = h

        rect_width = (BOX_WIDTH + BOX_PADDING
                      + title_width + COL_SPACE + info_width
                      + BOX_PADDING + BOX_WIDTH)
        rect_height = (BOX_WIDTH + BOX_PADDING
                       + (max_height * len(strs)) + (LINE_SPACE * (len(strs) - 1))
                       + BOX_PADDING + BOX_WIDTH)

        texts = list()
        y = BOX_WIDTH + BOX_PADDING
        for i, (s, title, font) in enumerate(zip(strs, TITLES, fonts)):
            texts += [
                (x1, y, title, {'fill': WHITE, 'font': title_font}),
                (x2, y, s, {'fill': WHITE, 'font': font}),
            ]
            y += max_height + LINE_SPACE

    code_bottom_y = heights['slider'] + heights['code']
    comment_top_y = heights['slider'] + heights['top']
    comment_bottom_y = comment_top_y + heights['comment']

    if fit_to_comment:
        pic_y1 = comment_top_y - COMMENT_PADDING
        pic_y2 = comment_bottom_y + COMMENT_PADDING
    else:
        pic_y1 = 0
        pic_y2 = max(code_bottom_y, comment_bottom_y) + heights['slider']

    tattoo_x = pic_width - X_PADDING - rect_width
    tattoo_y = pic_y2 - Y_PADDING - rect_height

    tattoo_corner = 'br'

    # TODO: if fitting to comment but comment is longer than code, then tattoo can go in bottom left

    # if fitting to comment, then always do bottom left corner
    # otherwise, choose best corner
    # priority is bottom right, top right, bottom left
    if not fit_to_comment and corner:
        # y space in each corner
        bottom_right = pic_y2 - comment_bottom_y - Y_PADDING
        top_right = comment_top_y - Y_PADDING
        bottom_left = pic_y2 - code_bottom_y - Y_PADDING

        # look for a corner in which the tattoo will completely fit
        if bottom_right >= rect_height:
            pass
        elif top_right >= rect_height:
            tattoo_corner = 'tr'
            tattoo_y = Y_PADDING
        elif bottom_left >= rect_height:
            tattoo_corner = 'bl'
            tattoo_x = X_PADDING
        else:
            # no corners fit perfectly, so find corner with most space
            if bottom_right >= top_right and bottom_right >= bottom_left:
                pass
            elif top_right >= bottom_right and top_right >= bottom_left:
                tattoo_corner = 'tr'
                tattoo_y = Y_PADDING
            elif bottom_left >= bottom_right and bottom_left >= top_right:
                tattoo_corner = 'bl'
                tattoo_x = X_PADDING
            else:
                # none were the max - impossible
                pass

    if adjust:
        # if diff < 0, tattoo is going to overlap the bottom of the comment
        # if diff < Y_PADDING, the tattoo is going to be too close to the comment
        if tattoo_corner == 'br':
            diff = tattoo_y - comment_bottom_y
            if diff < Y_PADDING:
                pic_y2 += -diff + Y_PADDING
                tattoo_y += -diff + Y_PADDING
        elif tattoo_corner == 'tr':
            diff = comment_top_y - (tattoo_y + rect_height)
            if diff < Y_PADDING:
                tattoo_y += diff - Y_PADDING
        elif tattoo_corner == 'bl':
            diff = tattoo_y - code_bottom_y
            if diff < Y_PADDING:
                pic_y2 += -diff + Y_PADDING
                tattoo_y += -diff + Y_PADDING

    pic_height = pic_y2 - pic_y1
    if pic_height > page.height:
        # resize page to accommodate screenshot
        await page.set_height(pic_height)

    # if fitting to comment, adjust `tattoo_y`
    if fit_to_comment:
        tattoo_y -= pic_y1

    if log: logger.debug('{}:{}: Taking screenshot', submission_id, comment_id)
    await page.screenshot(path=filepath, y=pic_y1, width=pic_width, height=pic_height)

    if log: logger.debug('{}:{}: Adding tattoo to image', submission_id, comment_id)
    # assignment name
    # submission id
    # comment id
    # file name
    # category name (if rubric comment)
    # comment name (if rubric comment)

    img = Image.open(filepath)

    # expand top if needed (should only happen for top right tattoo)
    # TODO test this
    if tattoo_y < Y_PADDING:
        add_top = -tattoo_y + Y_PADDING
        bg_color: Color = tuple(await page.evaluate(
            '''() => {
                const codeArea = document.getElementById("code-scroll-area");
                const style = codeArea.currentStyle || window.getComputedStyle(codeArea);
                return style.backgroundColor.match(/\\d+/g).map(Number);
            }'''
        ))
        img = ImageOps.expand(img, border=(0, add_top, 0, 0), fill=bg_color)
        tattoo_y = Y_PADDING

    img_draw = ImageDraw.Draw(img)

    # draw tattoo
    rectangle_coords = (tattoo_x, tattoo_y, tattoo_x + rect_width, tattoo_y + rect_height)
    rectangle_kwargs = {
        'fill': CODEPOST_GREEN,
        'outline': None,
        'width': BOX_WIDTH,
    }
    img_draw.rectangle(rectangle_coords, **rectangle_kwargs)

    # draw text
    for x, y, text, kwargs in texts:
        img_draw.text((tattoo_x + x, tattoo_y + y), text, **kwargs)

    img.save(filepath)

    if log: logger.info('{}:{}: Saved screenshot at "{}"', submission_id, comment_id, filepath)


# ===========================================================================

def get_file_name(submission_id: int,
                  comment_id: int,
                  rubric_id: Optional[int]
                  ) -> Tuple[str, Optional[str], Optional[str]]:
    """Gets the screenshot file name of the given comment.

    Args:
        submission_id (int): The submission id.
        comment_id (int): The comment id.
        rubric_id (Optional[int]): The rubric comment id, if the comment is a rubric comment.

    Returns:
        Tuple[str, Optional[str], Optional[str]]: The screenshot file name,
            the category name, and the comment name.
            The last two are None if the comment is not a rubric comment.
    """
    global RUBRIC_COMMENTS, RUBRIC_CATEGORIES

    if rubric_id is None:
        return FILES['screenshot'].format(submission_id, comment_id), None, None

    comment_name, category_name = RUBRIC_COMMENTS.get(rubric_id, (None, None))
    if None in (comment_name, category_name):
        rubric_comment = codepost.rubric_comment.retrieve(rubric_id)
        comment_name = rubric_comment.name
        category_id = rubric_comment.category
        category_name = RUBRIC_CATEGORIES.get(category_id, None)
        if category_name is None:
            category = codepost.rubric_category.retrieve(category_id)
            category_name = category.name
            RUBRIC_CATEGORIES[category_id] = category_name
        RUBRIC_COMMENTS[rubric_id] = (comment_name, category_name)

    screenshot_file = FILES['rubric comment'].format(submission_id, comment_id, comment_name)

    return screenshot_file, category_name, comment_name


async def create_screenshot(browser: pyppeteer.browser.Browser,
                            submission: Submission,
                            comment: Comment,
                            assignment_name: str,
                            file: File,
                            file_index: int,
                            output_folder: str,
                            timeout: int = 60,
                            explanation: bool = False,
                            fit_to_comment: bool = False,
                            one_line: bool = False,
                            corner: bool = False,
                            adjust: bool = False,
                            log: bool = False
                            ) -> bool:
    """Creates a screenshot for a comment.

    Args:
        browser (pyppeteer.browser.Browser): The browser.
        submission (Submission): The submission.
        comment (Comment): The comment.
        assignment_name (str): The assignment name.
        file (File): The file the comment belongs to.
        file_index (int): The index of the file in the Files section.
        output_folder (str): The path of the folder where the screenshot should be saved.
        timeout (int): The timeout limit for the page to load, in seconds.
            A timeout limit of 0 means no timeout.
            Default is 60 seconds.
        explanation (bool): Whether to show the comment explanation.
            Default is False.
        fit_to_comment (bool): Whether to fit the screenshot to the comment.
            Default is False.
        one_line (bool): Whether to make the tattoo one line.
            Default is False.
        corner (bool): Whether to optimize the corner of the tattoo.
            Default is False.
        adjust (bool): Whether to adjust the tattoo to not overlap the comment.
            Default is False.
        log (bool): Whether to show log messages.
            Default is False.

    Returns:
        bool: Whether the screenshot was successful.
    """

    # if fit to comment, make the tattoo one line to save image space
    if fit_to_comment: one_line = True

    submission_id = submission.id
    comment_id = comment.id

    strs = [assignment_name, str(submission_id), str(comment_id), file.name]

    screenshot_file, category_name, comment_name = get_file_name(submission_id, comment_id, comment.rubricComment)
    filepath = os.path.join(output_folder, screenshot_file)

    if (category_name, comment_name) != (None,) * 2:
        strs += [category_name, comment_name]

    # create new page
    page = await CodePostPage.create(browser, submission_id, explanation)

    if log: logger.debug('{}:{}: Loading page', submission_id, comment_id)
    start = time.time()
    successful = await page.open_submission(timeout=timeout)
    if not successful:
        if log: logger.warning('{}:{}: Timed out', submission_id, comment_id)
        return False
    end = time.time()
    if log: logger.debug('{}:{}: Loaded page ({:.2f})', submission_id, comment_id, end - start)

    if log: logger.debug('{}:{}: Selecting correct file', submission_id, comment_id)
    await page.select_file(file_index)

    # hide other comments
    if log: logger.debug('{}:{}: Hiding other comments', submission_id, comment_id)
    comments = [f'comment-{c.id}' for c in file.comments if c.id != comment_id]
    await page.hide_elements(ids=comments)

    # hide voting buttons (if rubric comment)
    if explanation and comment_name is not None:
        if log: logger.debug('{}:{}: Hiding voting buttons', submission_id, comment_id)
        await page.hide_voting(comment)

    # hide header bar, slider bar, section panel, command bar, and intercom
    if log: logger.debug('{}:{}: Hiding unwanted elements', submission_id, comment_id)
    elements = ['Code-Header', 'commandbar-wrapper', 'intercom-frame']
    hide_classes = ['layout--standard-console__header', 'intercom-lightweight-app']
    cover_classes = ['layout-resizer']
    await page.hide_elements(ids=elements, classes=hide_classes)
    await page.cover_elements(classes=cover_classes)

    # set column widths
    if log: logger.debug('{}:{}: Setting column widths', submission_id, comment_id)
    code_width = 600
    comment_width = 500
    await page.set_column_width(code=code_width, comment=comment_width)

    if log: logger.debug('{}:{}: Aligning comment', submission_id, comment_id)
    await page.align_comment(comment)

    # take screenshot
    await take_screenshot(submission_id, comment_id, filepath, page, strs,
                          fit_to_comment=fit_to_comment, one_line=one_line, corner=corner, adjust=adjust)

    return True


# ===========================================================================

async def create_screenshots(comments: Sequence[Tuple[Submission, Comment, str, File, int, str]],
                             timeout: int = 60000,
                             explanation: bool = False,
                             fit_to_comment: bool = False,
                             one_line: bool = False,
                             corner: bool = False,
                             adjust: bool = False,
                             log: bool = False
                             ):
    """Creates screenshots of the given comments.
    Adapted from https://gist.github.com/jlumbroso/c0ec0c4f1a0a502e3835c183cbe89c65 for SPA.

    Args:
        comments (Sequence[Tuple]): The comments to create screenshots for, in the format:
            [ (submission, comment, assignment name, file, file index, assignment folder) ]
        timeout (int): The timeout limit for the page to load, in seconds.
            A timeout limit of 0 means no timeout.
            Default is 60 seconds.
        explanation (bool): Whether to show the comment explanation.
            Default is False.
        fit_to_comment (bool): Whether to fit the screenshot to the comment.
            Default is False.
        one_line (bool): Whether to make the tattoo one line.
            Default is False.
        corner (bool): Whether to optimize the corner of the tattoo.
            Default is False.
        adjust (bool): Whether to adjust the tattoo to not overlap the comment.
            Default is False.
        log (bool): Whether to show log messages.
            Default is False.
    """

    # if fit to comment, make the tattoo one line to save image space
    if fit_to_comment: one_line = True

    if log: logger.info('Launching browser')
    start = time.time()
    browser = await launch()
    end = time.time()
    if log: logger.debug('Launched browser ({:.2f})', end - start)

    # store JWT
    if log: logger.debug('Storing JWT token')
    start = time.time()
    page = await browser.newPage()
    await page.goto(LOGIN_URL)
    await page.evaluate('(token) => { localStorage.setItem("token", token); }', JWT_KEY)
    await page.close()
    end = time.time()
    if log: logger.debug('Stored JWT token ({:.2f})', end - start)

    # create screenshot for all submissions
    screenshots = [
        create_screenshot(
            browser, *comment_info, timeout=timeout,
            explanation=explanation, fit_to_comment=fit_to_comment, one_line=one_line, corner=corner, adjust=adjust
        )
        for comment_info in comments
    ]
    # allows all screenshots to be generated synchronously as coroutines
    num_success = sum(await asyncio.gather(*screenshots))

    if log: logger.info('Successfully created {} out of {} screenshots', num_success, len(comments))

    if log: logger.debug('Closing browser')
    await browser.close()


# ===========================================================================

def main(link: str = None,
         file: str = None,
         timeout: int = 60,
         no_timeout: bool = False,
         explanation: bool = False,
         fit_to_comment: bool = False,
         one_line: bool = False,
         corner: bool = False,
         adjust: bool = False,
         log: bool = False
         ):
    """Screenshots a codePost comment.

    Args:
        link (str): The link of the codePost comment.
            Default is None.
        file (str): The file to read comments from.
            Default is None.
        timeout (int): The timeout limit per screenshot, in seconds.
            Must be at least 30.
            Default is 60 seconds.
        no_timeout (bool): Whether to run without timeout.
            Default is False.
        explanation (bool): Whether to show the comment explanation.
            Default is False.
        fit_to_comment (bool): Whether to fit the screenshot to the comment.
            Default is False.
        one_line (bool): Whether to make the tattoo one line.
            Default is False.
        corner (bool): Whether to optimize the corner of the tattoo.
            Default is False.
        adjust (bool): Whether to adjust the tattoo to not overlap the comment.
            Default is False.
        log (bool): Whether to show log messages.
            Default is False.

    Raises:
        ValueError: If `timeout` is not at least 30.
    """

    # check args
    if timeout < 30:
        raise ValueError('`timeout` must be at least 30')

    # if fit to comment, make the tattoo one line to save image space
    if fit_to_comment: one_line = True

    if no_timeout:
        timeout = 0

    # submission ids and comment ids
    comments: List[Tuple[int, int]] = list()

    if log: logger.info('Getting comments')

    if link is not None:
        s_id, c_id = extract_link(link, log=log)
        if s_id is not None and c_id is not None:
            comments.append((s_id, c_id))

    if file is not None:
        comments += read_comments_from_file(file, log=log)

    if len(comments) == 0:
        if log: logger.info('No comments to screenshot')
        return

    if not os.path.exists(SCREENSHOT_FOLDER):
        os.mkdir(SCREENSHOT_FOLDER)

    # maps assignment id -> assignment folder
    assignment_folders = dict()
    # maps assignment id -> assignment name
    assignment_names = dict()
    # the comment info to create screenshots
    comment_infos = list()

    # getting actual comments of ids
    for s_id, c_id in comments:

        submission = get_submission(s_id, log=log)
        if submission is None: continue
        comment = get_comment(c_id, s_id, log=log)
        if comment is None: continue

        file = codepost.file.retrieve(comment.file)
        file_index = sorted(f.name.lower() for f in submission.files).index(file.name.lower())

        # output folder
        a_id = submission.assignment
        assignment_folder = assignment_folders.get(a_id, None)
        if assignment_folder is None:
            # create assignment folder
            assignment = codepost.assignment.retrieve(a_id)
            course = codepost.course.retrieve(assignment.course)

            assignment_folder = get_path(path=SCREENSHOT_FOLDER, course=course, assignment=assignment)

            assignment_folders[a_id] = assignment_folder
            assignment_names[a_id] = assignment.name

        comment_infos.append(
            (submission, comment, assignment_names[a_id], file, file_index, assignment_folder)
        )

    if log: logger.info('Creating screenshots for {} comments', len(comment_infos))

    # TODO: how to keyboard interrupt
    asyncio.run(create_screenshots(
        comment_infos, timeout=timeout,
        explanation=explanation, fit_to_comment=fit_to_comment, one_line=one_line, corner=corner, adjust=adjust
    ))

# ===========================================================================
