#!/usr/bin/env python3
import argparse
import configparser
import json
import logging
import sys
from urllib import request
from urllib import parse
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CACHETHQ_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


class UptimeRobot(object):
    """ Intermediate class for setting uptime stats.
    """
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = 'https://api.uptimerobot.com/v2/getMonitors'

    def get_monitors(self, response_times=0, logs=0, uptime_ratio=30):
        """
        Returns status and response payload for all known monitors.
        """
        endpoint = self.base_url
        data = parse.urlencode({
                'api_key': format(self.api_key),
                'format': 'json',
                # responseTimes - optional (defines if the response time data of each
                # monitor will be returned. Should be set to 1 for getting them.
                # Default is 0)
                'response_times': format(response_times),
                # logs - optional (defines if the logs of each monitor will be
                # returned. Should be set to 1 for getting the logs. Default is 0)
                'logs': format(logs),
                # customUptimeRatio - optional (defines the number of days to calculate
                # the uptime ratio(s) for. Ex: customUptimeRatio=7-30-45 to get the
                # uptime ratios for those periods)
                'custom_uptime_ratios': format(uptime_ratio)
            }).encode('utf-8')

        url = request.Request(
            url=endpoint,
            data=data,
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Cache-Control': 'no-cache'
            },
        )

        # Verifying in the response is jsonp in otherwise is error
        response = request.urlopen(url)
        content = response.read().decode('utf-8')
        j_content = json.loads(content)
        if j_content.get('stat'):
            stat = j_content.get('stat')
            if stat == 'ok':
                return True, j_content

        return False, j_content


class CachetHq(object):
    # Uptime Robot status list
    UPTIME_ROBOT_PAUSED = 0
    UPTIME_ROBOT_NOT_CHECKED_YET = 1
    UPTIME_ROBOT_UP = 2
    UPTIME_ROBOT_SEEMS_DOWN = 8
    UPTIME_ROBOT_DOWN = 9

    # Cachet status list
    CACHET_OPERATIONAL = 1
    CACHET_PERFORMANCE_ISSUES = 2
    CACHET_SEEMS_DOWN = 3
    CACHET_DOWN = 4

    def __init__(self, cachet_api_key, cachet_url):
        self.cachet_api_key = cachet_api_key
        self.cachet_url = cachet_url

    def update_component(self, id_component=1, status=None):
        component_status = None

        # Not Checked yet and Up
        if status in [self.UPTIME_ROBOT_NOT_CHECKED_YET, self.UPTIME_ROBOT_UP]:
            component_status = self.CACHET_OPERATIONAL

        # Seems down
        elif status == self.UPTIME_ROBOT_SEEMS_DOWN:
            component_status = self.CACHET_SEEMS_DOWN

        # Down
        elif status == self.UPTIME_ROBOT_DOWN:
            component_status = self.CACHET_DOWN

        if component_status:
            url = '{0}/api/v1/{1}/{2}'.format(
                self.cachet_url,
                'components',
                id_component
            )
            data = {
                'status': component_status,
            }

            return self._request('PUT', url, data)

    def set_data_metrics(self, value, timestamp, id_metric=1):
        url = '{0}/api/v1/metrics/{1}/points'.format(
            self.cachet_url,
            id_metric
        )
        data = {
            'value': value,
            'timestamp': timestamp,
        }
        return self._request('POST', url, data)

    def get_last_metric_point(self, id_metric):
        url = '{0}/api/v1/metrics/{1}/points'.format(
            self.cachet_url,
            id_metric
        )
        api_response = self._request('GET', url)

        last_page = api_response.get('meta').get('pagination').get('total_pages')

        url = '{0}/api/v1/metrics/{1}/points?page={2}'.format(
            self.cachet_url,
            id_metric,
            last_page
        )
        api_response = self._request('GET', url)

        data = api_response.get('data')
        if data:
            # Return the latest data
            fmt = CACHETHQ_DATE_FORMAT
            created_at_dates = [
                datetime.strptime(datum['created_at'], fmt)
                for datum in data
            ]
            max_index = created_at_dates.index(max(created_at_dates))

            data = data[max_index]
        else:
            data = {
                'created_at': datetime.now().date().strftime(
                    CACHETHQ_DATE_FORMAT
                )
            }

        return data

    def _request(self, method, url, data=None):
        if data:
            data = parse.urlencode(data).encode('utf-8')

        req = request.Request(
            url=url,
            data=data,
            method=method,
            headers={
                'X-Cachet-Token': self.cachet_api_key,
                'Time-Zone': 'Etc/UTC',
            },
        )
        response = request.urlopen(req)
        content = response.read().decode('utf-8')

        return json.loads(content)


class Monitor(object):
    def __init__(self, monitor_list, api_key):
        self.monitor_list = monitor_list
        self.api_key = api_key

    def send_data_to_cachet(self, monitor):
        """ Posts data to Cachet API.
            Data sent is the value of last `Uptime`.
        """
        website_config = self._get_website_config(monitor)

        cachet = CachetHq(
            cachet_api_key=website_config['cachet_api_key'],
            cachet_url=website_config['cachet_url'],
        )

        if 'component_id' in website_config:
            cachet.update_component(
                website_config['component_id'],
                int(monitor.get('status'))
            )

        self.sync_metric(monitor, cachet)

    def sync_metric(self, monitor, cachet):
        website_config = self._get_website_config(monitor)
        latest_metric = cachet.get_last_metric_point(website_config['metric_id'])

        logger.info('Number of response times: %d', len(monitor['response_times']))
        logger.info('Latest metric: %s', latest_metric)
        unixtime = self._date_str_to_unixtime(latest_metric['created_at'])

        response_times = [
            x for x in monitor['response_times']
            if x['datetime'] > unixtime
        ]
        response_times.sort(key=lambda x: x['datetime'])

        logger.info('Number of new response times: %d', len(response_times))

        for response_time in response_times:
            metric = cachet.set_data_metrics(
                response_time['value'],
                response_time['datetime'],
                website_config['metric_id']
            )
            logger.info('Metric created: %s', metric)

    def update(self):
        """ Update all monitors uptime and status.
        """
        uptime_robot = UptimeRobot(self.api_key)
        success, response = uptime_robot.get_monitors(response_times=1)
        if success:
            monitors = response.get('monitors')
            for monitor in monitors:
                if monitor['url'] in self.monitor_list:
                    logger.info(
                        'Updating monitor %s. URL: %s. ID: %s',
                        monitor['friendly_name'],
                        monitor['url'],
                        monitor['id']
                    )
                    self.send_data_to_cachet(monitor)
        else:
            logger.error('No data was returned from UptimeMonitor')

    def _get_website_config(self, monitor):
        try:
            return self.monitor_list[monitor.get('url')]
        except KeyError:
            logger.error('Monitor is not valid')
            sys.exit(1)

    def _date_str_to_unixtime(self, date_str, fmt=CACHETHQ_DATE_FORMAT, tzinfo=timezone.utc):
        unixtime = datetime.strptime(date_str, fmt) \
                           .replace(tzinfo=tzinfo) \
                           .timestamp()

        return int(unixtime)


def main():
    args = parse_args()
    monitor_dict, uptime_robot_api_key = parse_config(args.config_file)

    Monitor(monitor_list=monitor_dict, api_key=uptime_robot_api_key).update()


def parse_args():
    parser = argparse.ArgumentParser(description='Send data from UptimeRobot to CachetHQ')

    parser.add_argument(
        'config_file',
        nargs='?',
        type=argparse.FileType('r'),
        help='path to the configuration file (default: config.ini in current folder)',
        default='config.ini'
    )

    return parser.parse_args()


def parse_config(config_file):
    config = configparser.ConfigParser()
    config.read_file(config_file)

    if not config.sections():
        logger.error('File path is not valid')
        sys.exit(1)

    uptime_robot_api_key = None
    monitor_dict = {}
    for element in config.sections():
        if element == 'uptimeRobot':
            uptime_robot_api_key = config[element]['UptimeRobotMainApiKey']
        else:
            monitor_dict[element] = {
                'cachet_api_key': config[element]['CachetApiKey'],
                'cachet_url': config[element]['CachetUrl'],
                'metric_id': config[element]['MetricId'],
            }
            if 'ComponentId' in config[element]:
                monitor_dict[element].update({
                    'component_id': config[element]['ComponentId'],
                })

    return monitor_dict, uptime_robot_api_key


if __name__ == "__main__":
    main()
