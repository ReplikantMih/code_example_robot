import asyncio
import websockets
import json
from time import sleep
from datetime import datetime, timedelta
import traceback
import os


class Robot:
    def __init__(self):
        self.api_link = 'wss://test.deribit.com/ws/api/v2'
        self.key = ''
        self.secret = 'MfXk5OT-'
        self.symbol = 'BTC-PERPETUAL'
        self.iteration_wait = 5  # Пауза между итерациями, sec.
        self.log_path = 'wss_deribit_robot_log.txt'  # Путь до файла с логами.
        self.logging_on = True  # Включено ли логирование.
        self.price_indent = 5  # Размер заступа за лучшую цену противоположной стороны сделки в стакане.
        self.trade_wait = 1  # Ожидание до сделки, минут.
        self.clear_console = False  # Очищать ли консоль при обновлении информации.
        self.connection_timeout = 1  # Таймаут при ожидании ответа от сервера, при превышении, считаем, что не достучались.
        # GLOBAL VARIABLES:
        self.wss_connection = None  # Коннект с сокетом Deribit.
        self.tokens = None  # Токены.
        self.upper_price_limit = None  # Заданный пользователем лимит цены сверху.
        self.user_volume = None  # Заданный пользователем объем.
        self.position_volume = 0  # Объем открытой позиции.
        self.unrealized_profit = 0  # Нереализованный профит.
        self.total_profit = 0  # Общий профит.
        self.position_direction = None  # Направление текущей позиции.
        self.status_info = 'OK'  # Информация об ошибках и прочая статусная инфомрация.
        self.time_point = None  # Точка во времени, по которой совершаем сделку.
        self.open_direction = 'buy'  # В какую сторону сейчас открываемся.
        self.connection_ok = True  # Текущее состояние соединения.

        asyncio.get_event_loop().run_until_complete(self.process())

    async def process(self):
        self.user_data_input()
        while True:
            await self.reconnect_if_disconnected()
            if not self.wss_connection:
                sleep(self.iteration_wait)
                continue

            connection_check = await self._check_connection()
            self.show_updated_data()
            if not connection_check:
                sleep(self.iteration_wait)
                self.wss_connection = None
                continue
            # Если не в позиции.
            if not self.position_volume:
                await self.open_position()
            # Если в позиции.
            else:
                await self.close_position()
            sleep(self.iteration_wait)

    # Закрытие позиции.
    async def close_position(self):
        while (
                datetime.now() > self.time_point or await self.get_last_depersonalized_trade() > self.upper_price_limit) and \
                self.position_volume:
            position = await self.get_position()
            volume = position['result']['size']
            price = await self.get_best_bid()
            await self.sell_limit(price, volume, '')
            sleep(self.iteration_wait)
            await self.update_data()
            self.show_updated_data()
            self.time_point = datetime.now() + timedelta(minutes=self.trade_wait)

    # Открытие позиции.
    async def open_position(self):
        while datetime.now() > self.time_point and self.position_volume < self.user_volume:
            position = await self.get_position()
            price = await self.get_best_offer() + self.price_indent
            volume = round((self.user_volume - position['result']['size']) / 10) * 10
            await self.buy_limit(price, volume, '')
            sleep(self.iteration_wait)
            await self.update_data()
            self.show_updated_data()
            self.time_point = datetime.now() + timedelta(minutes=self.trade_wait)

    # Отобразить актуальные данные пользователю.
    def show_updated_data(self):
        if self.clear_console:
            if os.name.lower() == 'nt':
                os.system('cls')
            else:
                os.system('clear')
        else:
            print('\n' * 0)
        print(f'\nConnection:                 {"OK" if self.connection_ok else "DISCONNECTED"}')
        print(f'Position volume:            {self.position_volume}')
        print(f'Position direction:         {self.position_direction}')
        print(f'Position unrealized profit: {self.unrealized_profit}')
        print(f'Total profit:               {self.total_profit}')
        print(f'Status info:                {self.status_info}\n')

    # Актуализировать данные по торговле.
    async def update_data(self):
        position = await self.get_position()
        self.position_volume = position['result']['size']
        self.position_direction = position['result']['direction']
        self.unrealized_profit = position['result']['floating_profit_loss']
        self.total_profit = position['result']['total_profit_loss']

    # Проверяем наличие коннекта, если отсутствует - восстанавливаем.
    async def reconnect_if_disconnected(self):
        if not self.wss_connection:
            try:
                self.wss_connection = await websockets.connect(self.api_link)
                await self._get_tokens()
            except:
                self._log(traceback.format_exc(), 'file')
                self._log('connection error', 'console')
                self.wss_connection = None
                result = 'Error: API_call error.'
                self.status_info = result

    # Ввод пользовательских данных - лимит цены, объем.
    def user_data_input(self):
        while True:
            try:
                volume_input = int(input('Введите объем позиции: '))
                if volume_input % 10 != 0:
                    print('Объем позиции должен быть кратен 10, повторите ввод.')
                    continue
                break
            except:
                self._log('Ошибка ввода, повторите ввод')
        self.user_volume = volume_input
        while True:
            try:
                self.upper_price_limit = float(input('Введите предельную цену: '))
                self.time_point = datetime.now() + timedelta(minutes=self.trade_wait)
                break
            except:
                self._log('Ошибка ввода, повторите ввод')

    # TRADING METHODS:
    async def get_position(self):
        result = await self._process_api_call({'instrument_name': self.symbol}, 'private/get_position', True, True)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось открыть позицию.'
            return None

        return result

    async def get_order_state(self, order_id):
        result = await self._process_api_call({'order_id': order_id}, 'private/get_order_state', True, True)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось открыть позицию.'
            return None

        return result

    async def buy_limit(self, price, volume, label):
        params = \
            {'type': 'limit',
             'price': price,
             'amount': volume,
             'instrument_name': self.symbol,
             'time_in_force': 'immediate_or_cancel',
             'label': str(label)
             }
        result = await self._process_api_call(params, 'private/buy', True, True)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось купить.'
            return None

        return result['result']['order']['order_id']

    async def sell_limit(self, price, volume, label):
        params = \
            {'type': 'limit',
             'price': price,
             'amount': volume,
             'instrument_name': self.symbol,
             'time_in_force': 'immediate_or_cancel',
             'label': str(label)
             }
        result = await self._process_api_call(params, 'private/sell', True, True)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось продать.', 'console')
            return None
        return result['result']['order']['order_id']

    async def get_last_depersonalized_trade(self):
        result = await self._process_api_call({"instrument_name": self.symbol, "count": 1},
                                              'public/get_last_trades_by_instrument', False, False)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось открыть позицию.'
            return {}
        print(result)
        return result['result']['trades'][0]['price']

    async def get_best_bid(self):
        params = \
            {"instrument_name": self.symbol,
             "depth": 1}
        result = await self._process_api_call(params, 'public/get_order_book', False, False)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось лучшую цену bid.'
            return None

        return float(result['result']['best_bid_price'])

    async def get_best_offer(self):
        params = \
            {"instrument_name": self.symbol,
             "depth": 1}
        result = await self._process_api_call(params, 'public/get_order_book', False, False)
        if result.get('error', None):
            self._log(f'Error: {traceback.format_exc()}', 'file')
            self._log(f'Error: не удалось открыть позицию.', 'console')
            self.status_info = 'Error: не удалось получить значение лучшей цены aks.'
            return None

        return float(result['result']['best_ask_price'])

    async def _get_tokens(self):
        self.tokens = await self._process_api_call({"grant_type": "client_credentials"}, 'public/auth', True)

    # SYSTEM METHODS:
    def _log(self, text, log_type='all'):  # log_type in ['console', 'file', 'all'].
        if self.logging_on:
            text = f'{datetime.now()} - {text}'
            if log_type == 'console' or log_type == 'all':
                print(text)
            if log_type == 'file' or log_type == 'all':
                with open(self.log_path, 'a', encoding='utf-8') as file:
                    file.write(text)

    async def _call_api(self, data):
        connection_check_result = await self._check_connection()
        if not connection_check_result:
            return False
        await self.wss_connection.send(json.dumps(data))
        response = await self.wss_connection.recv()
        response = json.loads(response)
        return response

    async def _process_api_call(self, params, method, is_private, tokens_needed=False):
        self._log('api_call:')
        if is_private:
            params['client_id'] = self.key
            params['client_secret'] = self.secret
        if tokens_needed:
            params['access_token'] = self.tokens['result']['access_token']
        data = \
            {
                'jsonrpc': '2.0',
                'method': method,
                'params': params
            }
        self._log(f'method: {method} - params: {params}')
        try:
            result = await self._call_api(data)
            self._log(f'result: {result}')
            if not result:
                return False
        except:
            self._log(traceback.format_exc())  # TMP
            self._log(traceback.format_exc(), 'file')
            self._log('connection error', 'console')
            self.wss_connection = None
            result = {}
            self.status_info = result
        return result

    async def _check_connection(self):  # Пингуем сервер Deribit, если ОК - возвращает True, иначе False.
        try:
            pong = await self.wss_connection.ping()
            while pong:
                pong = await asyncio.wait_for(pong, timeout=self.connection_timeout)
                self.connection_ok = True
                return True
        except:
            result = 'Error: сервер биржи не пингуется.'
            self._log(result)
            self.status_info = result
            self.wss_connection = None
            self.connection_ok = False
        return False


robot = Robot()
