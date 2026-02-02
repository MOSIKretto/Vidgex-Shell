import setproctitle
from crosspad import start_recognition


if __name__ == '__main__':
    # Установка имени процесса
    setproctitle.setproctitle("vidgex-eyes-hands")

    # Запуск логики из первого файла
    start_recognition()