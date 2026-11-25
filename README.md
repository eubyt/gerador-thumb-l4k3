# Thumbs para o L4k3

Prova de conceito para criar thumbs de YouTube Shorts para o streamer L4k3. O fluxo pode ser usado com qualquer agente que consiga ler os arquivos do projeto e gerar imagens.

## Como usar com um agente

Abra a pasta do projeto no agente, indique o [AGENTS.md](AGENTS.md) como guia e informe o tema do vídeo, a frase e, se quiser, a expressão do streamer.

> Crie uma thumb para um Short sobre aliens no GTA VI. Use a frase “Eles estão lá fora” e a expressão de surpresa.

## Como funciona

Instale o Pillow no Python do computador, a partir da pasta do projeto.

```sh
python3 -m pip install --user -r requirements.txt
```

1. Prepare o pedido com o tema e a frase. O comando imprime o caminho da pasta temporária do pedido.

   ```sh
   python3 criar_thumb.py preparar \
     --texto "Eles estão lá fora" \
     --contexto "Alienígenas e OVNIs no GTA VI" \
     --expressao surpreso
   ```

2. Use o `prompt-arte.txt` dessa pasta para gerar o cartaz com a ferramenta de imagem do agente, como o ImageGen. As referências de estilo ficam em `ref/thumbnails/`.
3. Passe o caminho do pedido e a imagem do cartaz ao script.

   ```sh
   python3 criar_thumb.py finalizar \
     --job "/caminho/do/pedido" \
     --arte "/caminho/do/cartaz.png"
   ```

O Python monta a thumb com o retrato escolhido, a frase, a badge, a moldura e o cartaz. A imagem final tem 1080 × 1920 pixels e fica em `output/thumb.png`.
