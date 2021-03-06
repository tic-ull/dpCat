# encoding: utf-8
from postproduccion.models import Cola, HistoricoCodificacion
from postproduccion.video import create_pil, create_preview, copy_video
from django.conf import settings
from configuracion import config
from postproduccion import log, token, utils

import os
import re
import tempfile
import signal
from datetime import datetime


def enqueue_copy(v):
    """
    Encola el video de tipo normal dado para que sea copiado.
    """
    log.copy_enqueue(v)
    c = Cola(video=v, tipo='COP')
    c.save()


def enqueue_pil(v):
    """
    Encola el video de tipo píldora dado para que sea montado.
    """
    log.pil_enqueue(v)
    c = Cola(video=v, tipo='PIL')
    c.save()


def enqueue_preview(v):
    """
    Encola un vídeo para generar su previsualización.
    """
    log.preview_enqueue(v)
    c = Cola(video=v, tipo='PRE')
    c.save()


def make_pid_notifier(task):
    """
    Crea una funcion de notificación de PID para la tarea dada.
    """
    """
    Almacena el PID del proceso en ejecución
    """
    def pid_notifier(pid):
        task.pid = pid
        task.save()

    return pid_notifier


def process_task(task):
    """
    Procesa el elemento dado de la cola.
    """
    error = False

    # Crea el fichero para el registro de la salida de codificación.
    (handle, path) = tempfile.mkstemp(
        suffix='.log',
        dir=settings.MEDIA_ROOT + '/logs'
    )

    # Actualiza la información de la base de datos.
    task.logfile = 'logs/' + os.path.basename(path)
    task.comienzo = datetime.now()
    task.status = 'PRO'
    task.save()

    if task.tipo == 'COP':
        log.copy_start(task.video)
        if copy_video(task.video, handle):
            log.copy_finish(task.video)
            if task.video.status == 'COM':
                enqueue_preview(task.video)
        else:
            log.copy_error(task.video)
            error = True

    if task.tipo == 'PIL':
        log.pil_start(task.video)
        if create_pil(task.video, handle, make_pid_notifier(task)):
            log.pil_finish(task.video)
            if task.video.status == 'COM':
                enqueue_preview(task.video)
        else:
            log.pil_error(task.video)
            error = True

    if task.tipo == 'PRE':
        log.preview_start(task.video)
        if create_preview(task.video, handle, make_pid_notifier(task)):
            log.preview_finish(task.video)
            token.send_mail_to_user(task.video)
        else:
            log.preview_error(task.video)
            error = True

    # Cierra el fichero de registro
    os.close(handle)

    # Actualiza la información de la base de datos.
    task.fin = datetime.now()
    task.status = 'HEC' if not error else 'ERR'
    task.save()


def progress(task):
    """
    Devuelve el progreso de codificación de una tarea.
    """
    if task.status == 'PEN':
        return 0
    if task.status == 'HEC':
        return 100

    if task.tipo == 'COP':
        src = task.video.ficheroentrada_set.all()[0].fichero
        dst = task.video.fichero
        try:
            src_size = os.stat(src).st_size
            dst_size = os.stat(dst).st_size
            return int(float(dst_size) * 100 / float(src_size))
        except:
            return 0

    fd = os.open(task.logfile.path, os.O_RDONLY)
    try:
        os.lseek(fd, -255, os.SEEK_END)
        data = os.read(fd, 255)
        if task.tipo == 'PIL':
            pro = int(re.findall(' percentage: *([0-9]*)', data)[-1])
        if task.tipo == 'PRE':
            pro = int(utils.time_to_seconds(re.findall(
                ' time=([^=]*) ',
                data
            )[-1]) * 100 / task.video.tecdata.duration)
    except:
        pro = 0

    if pro < 0 or pro > 100:
        pro = 0

    os.close(fd)

    return pro


def removeCompleted():
    """
    Elimina de la lista los trabajos completados.
    """
    Cola.objects.filter(status='HEC').delete()


def removeVideoTasks(v):
    """
    Elimina los trabajos asociados a un vídeo.
    """
    tasks = Cola.objects.filter(video=v).order_by('pk')
    for t in tasks:
        hist = HistoricoCodificacion(
            informe=v.informeproduccion,
            tipo=t.tipo,
            fecha=t.comienzo,
            status=(t.status == 'HEC')
        )
        hist.save()
    tasks.delete()


def available_slots():
    """
    Devuelve el número de puestos libres para
    iniciar el proceso de codificación.
    """
    return int(config.get_option('MAX_ENCODING_TASKS')) - \
        Cola.objects.count_actives()


def cancel_task(task):
    """
    Cancela la ejecución de una tarea.
    """
    if task.status == 'PRO' and task.pid:
        try:
            os.kill(task.pid, signal.SIGTERM)
        except:
            task.status = 'ERR'
            task.save()
        while Cola.objects.get(pk=task.id).status == 'PRO': pass


def get_log(task):
    """
    Devuelve el contenido del log de una tarea.
    """
    task.logfile.file.open()
    data = task.logfile.file.read()
    task.logfile.file.close()
    return data
