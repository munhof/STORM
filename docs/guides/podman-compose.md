# Vista local con Podman Compose

Desde la raíz del repositorio, iniciá Studio y el worker:

```bash
podman-compose up --build -d
```

Abrí <http://127.0.0.1:8000>. Los paquetes de Studio y del motor se montan en
modo de solo lectura, así que Django vuelve a cargar los cambios de Python y
plantillas. Si cambian las dependencias, reconstruí la imagen con el mismo
comando.

El servicio reutiliza `.storm/` para la base local y los artefactos. `down`
detiene los contenedores y conserva esos datos:

```bash
podman-compose ps
podman-compose logs -f studio worker
podman-compose down
```

El puerto se publica sólo en `127.0.0.1`. El worker recupera el archivo de
bloqueo si la identidad registrada ya no corresponde a un proceso activo. En
contenedores también comprueba el comando del proceso para no confundir el PID
del worker anterior con el proceso de inicialización del contenedor nuevo.
