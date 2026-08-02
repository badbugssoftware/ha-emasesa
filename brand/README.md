# Recursos de marca

Icono **original** de esta integración no oficial: una gota de agua con la
silueta estilizada de la Giralda y las ondas del Guadalquivir.

- `icon.svg` — fuente vectorial (edítala aquí y reexporta los PNG).
- `icon.png` (256×256) / `icon@2x.png` (512×512)
- `logo.png` / `logo@2x.png`

## Aviso

Este dibujo es una creación propia. **No reproduce el logotipo de EMASESA**
(marca registrada de su titular) ni el emblema municipal de Sevilla (NO8DO).
La Giralda es un monumento histórico de los siglos XII–XVI y lo representado
es una interpretación simplificada.

## Cómo aparece en Home Assistant

Home Assistant toma los iconos del repositorio
[home-assistant/brands](https://github.com/home-assistant/brands). Para que se
vea en HACS y en la interfaz hay que enviar allí un PR con estos PNG en
`custom_integrations/emasesa/`.

Para reexportar los PNG desde el SVG (macOS):

```bash
qlmanage -t -s 1024 -o /tmp/iconout brand/icon.svg
sips -z 256 256 /tmp/iconout/icon.svg.png --out brand/icon.png
sips -z 512 512 /tmp/iconout/icon.svg.png --out brand/icon@2x.png
```
