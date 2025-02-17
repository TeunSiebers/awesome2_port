import logging
import json
import io
from typing import Optional, Literal


import pandas as pd

import port.api.props as props
import port.helpers as helpers
import port.validate as validate
import port.tiktok as tiktok

from port.api.commands import (CommandSystemDonate, CommandUIRender, CommandSystemExit)

LOG_STREAM = io.StringIO()

logging.basicConfig(
    #stream=LOG_STREAM,
    level=logging.DEBUG,
    format="%(asctime)s --- %(name)s --- %(levelname)s --- %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

LOGGER = logging.getLogger("script")


def process(session_id):
    LOGGER.info("Starting the donation flow")
    yield donate_logs(f"{session_id}-tracking")

    platforms = [ ("TikTok", extract_tiktok, tiktok.validate), ]

    # progress in %
    subflows = len(platforms)
    steps = 2
    step_percentage = (100 / subflows) / steps
    progress = 0

    # For each platform
    # 1. Prompt file extraction loop
    # 2. In case of succes render data on screen
    for platform in platforms:
        platform_name, extraction_fun, validation_fun = platform

        table_list = None
        progress += step_percentage

        # Prompt file extraction loop
        while True:
            LOGGER.info("Prompt for file for %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Render the propmt file page
            promptFile = prompt_file("application/zip, text/plain, application/json", platform_name)
            file_result = yield render_donation_page(platform_name, promptFile, progress)

            if file_result.__type__ == "PayloadString":
                validation = validation_fun(file_result.value)

                # DDP is recognized: Status code zero
                if validation.status_code.id == 0: 
                    LOGGER.info("Payload for %s", platform_name)
                    yield donate_logs(f"{session_id}-tracking")

                    table_list = extraction_fun(file_result.value, validation)
                    break

                # DDP is not recognized: Different status code
                if validation.status_code.id != 0: 
                    LOGGER.info("Not a valid %s zip; No payload; prompt retry_confirmation", platform_name)
                    yield donate_logs(f"{session_id}-tracking")
                    retry_result = yield render_donation_page(platform_name, retry_confirmation(platform_name), progress)

                    if retry_result.__type__ == "PayloadTrue":
                        continue
                    else:
                        LOGGER.info("Skipped during retry %s", platform_name)
                        yield donate_logs(f"{session_id}-tracking")
                        break
            else:
                LOGGER.info("Skipped %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                break

        progress += step_percentage

        # Render data on screen
        if table_list is not None:
            LOGGER.info("Prompt consent; %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Check if extract something got extracted
            if len(table_list) == 0:
                table_list.append(create_empty_table(platform_name))

            prompt = assemble_tables_into_form(table_list)
            consent_result = yield render_donation_page(platform_name, prompt, progress)

            if consent_result.__type__ == "PayloadJSON":
                LOGGER.info("Data donated; %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                yield donate(platform_name, consent_result.value)
            else:
                LOGGER.info("Skipped ater reviewing consent: %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")

    yield exit(0, "Success")
    yield render_end_page()



##################################################################

def assemble_tables_into_form(table_list: list[props.PropsUIPromptConsentFormTable]) -> props.PropsUIPromptConsentForm:
    """
    Assembles all donated data in consent form to be displayed
    """
    return props.PropsUIPromptConsentForm(table_list, [])


def donate_logs(key):
    log_string = LOG_STREAM.getvalue()  # read the log stream
    if log_string:
        log_data = log_string.split("\n")
    else:
        log_data = ["no logs"]

    return donate(key, json.dumps(log_data))


def create_empty_table(platform_name: str) -> props.PropsUIPromptConsentFormTable:
    """
    Show something in case no data was extracted
    """
    title = props.Translatable({
       "en": "Er ging niks mis, maar we konden niks vinden",
       "nl": "Er ging niks mis, maar we konden niks vinden"
    })
    df = pd.DataFrame(["No data found"], columns=["No data found"])
    table = props.PropsUIPromptConsentFormTable(f"{platform_name}_no_data_found", title, df)
    return table


##################################################################
# Visualization helpers
def create_chart(type: Literal["bar", "line", "area"], 
                 nl_title: str, en_title: str, 
                 x: str, y: Optional[str] = None, 
                 x_label: Optional[str] = None, y_label: Optional[str] = None,
                 date_format: Optional[str] = None, aggregate: str = "count", addZeroes: bool = True):
    if y is None:
        y = x
        if aggregate != "count": 
            raise ValueError("If y is None, aggregate must be count if y is not specified")
        
    return props.PropsUIChartVisualization(
        title = props.Translatable({"en": en_title, "nl": nl_title}),
        type = type,
        group = props.PropsUIChartGroup(column= x, label= x_label, dateFormat= date_format),
        values = [props.PropsUIChartValue(column= y, label= y_label, aggregate= aggregate, addZeroes= addZeroes)]       
    )

def create_wordcloud(nl_title: str, en_title: str, column: str, 
                     tokenize: bool = False, 
                     value_column: Optional[str] = None, 
                     extract: Optional[Literal["url_domain"]] = None):
    return props.PropsUITextVisualization(title = props.Translatable({"en": en_title, "nl": nl_title}),
                                          type='wordcloud',
                                          text_column=column,
                                          value_column=value_column,
                                          tokenize=tokenize,
                                          extract=extract)


##################################################################
# Extraction functions

def extract_tiktok(tiktok_file: str, validation: validate.ValidateInput) -> list[props.PropsUIPromptConsentFormTable]:
    tables_to_render = []

    df = tiktok.video_browsing_history_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok video browsing history", "nl": "Tiktok video browsing history"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_video_browsing_history", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.favorite_videos_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok favorite videos", "nl": "Tiktok favorite videos"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_favorite_videos", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.following_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok following", "nl": "Tiktok following"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_following", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.like_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok likes", "nl": "Tiktok likes"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_like", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.search_history_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok search history", "nl": "Tiktok search history"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_search_history", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.share_history_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok share history", "nl": "Tiktok share history"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_share_history", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.comment_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok comment history", "nl": "Tiktok comment history"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_comment", table_title, df) 
        tables_to_render.append(table)

    df = tiktok.watch_live_history_to_df(tiktok_file, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok watch live history", "nl": "Tiktok watch live history"})
        table =  props.PropsUIPromptConsentFormTable("tiktok_watch_live_history", table_title, df) 
        tables_to_render.append(table)

    return tables_to_render



##########################################
# Functions provided by Eyra did not change

def render_end_page():
    page = props.PropsUIPageEnd()
    return CommandUIRender(page)


def render_donation_page(platform, body, progress):
    header = props.PropsUIHeader(props.Translatable({"en": platform, "nl": platform}))

    footer = props.PropsUIFooter(progress)
    page = props.PropsUIPageDonation(platform, header, body, footer)
    return CommandUIRender(page)


def retry_confirmation(platform):
    text = props.Translatable(
        {
            "en": f"Unfortunately, we could not process your {platform} file. If you are sure that you selected the correct file, press Continue. To select a different file, press Try again.",
            "nl": f"Helaas, kunnen we uw {platform} bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen."
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file(extensions, platform):
    description = props.Translatable(
        {
            "en": f"Please follow the download instructions and choose the file that you stored on your device. Click “Skip” at the right bottom, if you do not have a file from {platform}.",
            "nl": f"Volg de download instructies en kies het bestand dat u opgeslagen heeft op uw apparaat. Als u geen {platform} bestand heeft klik dan op “Overslaan” rechts onder."
        }
    )
    return props.PropsUIPromptFileInput(description, extensions)


def donate(key, json_string):
    return CommandSystemDonate(key, json_string)

def exit(code, info):
    return CommandSystemExit(code, info)
