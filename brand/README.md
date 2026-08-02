# Recursos de marca

Marca de la integración, tomada del **EMASESA Design System**. Es una **creación
propia** —una gota de agua con la silueta de la Giralda en negativo sobre las
ondas del río— y **no reproduce el logotipo de EMASESA**, que es marca
registrada de su titular, ni el emblema municipal de Sevilla (NO8DO). La
Giralda es un monumento histórico de los siglos XII–XVI y lo representado es una
interpretación simplificada.

## Variantes

Están en `logo/`, todas con `viewBox="0 0 256 256"`:

| Archivo | Cuándo usarla |
|---|---|
| `logo-primario.svg` | Fondos blancos y grises. **Es la de referencia.** |
| `logo-negativo.svg` | Fondos azul 900 y 950 |
| `logo-sobre-cyan.svg` | Únicamente sobre cyan 500 |
| `logo-una-tinta.svg` | Por debajo de 24 px, grabado, bordado, sello |
| `logo-una-tinta-oscura.svg` | Reducción en azul 950 |
| `logo-una-tinta-blanca.svg` | Reducción en blanco |

### Reglas

- **Por debajo de 24 px usar `una-tinta`**: a ese tamaño el cambio de tono y el
  anillo se cierran y la torre deja de leerse.
- Sobre cyan 500 **nunca** `primario` ni `negativo`: solo `sobre-cyan`.
- Aire de respeto: la anchura del fuste (0,16 del lado del símbolo).
- No estirar, no rotar, no cambiar el orden de los tonos, no añadir sombra.

## Iconos de la integración

| Archivo | Uso |
|---|---|
| `icon.png` (256×256) · `icon@2x.png` (512×512) | Icono de la integración |
| `logo.png` / `logo@2x.png` | Copias del icono |
| `icon-dark.png` | Variante negativa, por si hiciera falta en tema oscuro |

Van recortados (`viewBox="12 10 232 232"`) para que la gota toque el borde
superior e inferior, como piden los iconos de aplicación. **La versión de marca
con su aire de respeto es la de `logo/`**, no estas.

La torre va en negativo, así que **toma el color del fondo**: el `primario`
funciona tanto en el tema claro como en el oscuro de Home Assistant.

Los mismos PNG están duplicados en `custom_components/emasesa/brand/`, que es
donde los busca la validación de HACS.

## Reexportar

Con [librsvg](https://formulae.brew.sh/formula/librsvg) (`brew install librsvg`).
**No uses `qlmanage`**: aplana el resultado sobre blanco y se pierde la
transparencia.

```bash
rsvg-convert -w 256 -h 256 -o brand/icon.png    brand/logo/logo-primario.svg
rsvg-convert -w 512 -h 512 -o brand/icon@2x.png brand/logo/logo-primario.svg
```

## Que aparezca en Home Assistant

Home Assistant toma los iconos de
[home-assistant/brands](https://github.com/home-assistant/brands). Para que se
vea en HACS y en la interfaz hay que enviar allí un PR con estos PNG en
`custom_integrations/emasesa/`.
