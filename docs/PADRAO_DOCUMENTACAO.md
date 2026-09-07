# Padrão de documentação do ecossistema GAIA

Este guia vale para os projetos GAIA, ARGUS, ECHO, ERIS, HESTIA, IRIS, LOKI, MOIRAI, PANDORA e SIREN.

## Escrita

- Use português simples e direto.
- Prefira frases curtas e uma ideia por parágrafo.
- Mantenha cada parágrafo em uma única linha no arquivo Markdown.
- Deixe o editor fazer a quebra visual de linhas longas.
- Use passos numerados quando a ordem for importante.
- Use listas para requisitos, recursos e opções.
- Explique siglas e termos técnicos na primeira vez que aparecerem.
- Preserve nomes de comandos, arquivos, classes e rotas entre crases.
- Registre fatos. Evite tom de propaganda e adjetivos que não ajudam.

Evite estes padrões:

- travessão longo;
- contraste artificial que primeiro nega uma definição e depois apresenta outra;
- frases em caixa alta para dar ênfase;
- parágrafos longos com muitos parênteses;
- palavras rebuscadas quando houver uma opção comum com o mesmo sentido.

## README

O `README.md` fica na raiz porque o GitHub o mostra na página inicial do repositório. Ele deve permitir que uma pessoa entenda e execute o projeto sem precisar abrir a documentação interna.

Use as seções que fizerem sentido, nesta ordem:

1. nome e resumo em uma frase;
2. recursos principais;
3. origem do nome e identidade visual;
4. requisitos;
5. instalação;
6. como usar;
7. integrações;
8. documentação;
9. situação atual.

Não copie a arquitetura completa para o README. Coloque o detalhe em `docs/` e deixe um link claro.

### Origem do nome e identidade visual

- Explique de onde vem o nome, o significado e a ligação com a função do projeto.
- Descreva somente os elementos que aparecem na logo real.
- Relacione um símbolo da origem do nome a um símbolo da função do software.
- Considere se a forma principal continua reconhecível em tamanho pequeno.
- Preserve a unidade do ecossistema com branco ou prata, azul e dourado quando essas cores combinarem com o projeto. Cores próprias podem destacar um domínio específico, como o roxo do PANDORA.

A regra visual do ecossistema é unir mito e função em um único símbolo. Exemplos: pavão e olhos para vigilância no ARGUS; fogo e vapor para Steam no HESTIA; fio e reprodução para animes no MOIRAI; sereia e ondas marítimas ou sonoras no SIREN.

### Integrações

Não cite outros projetos na apresentação inicial. Use uma seção própria depois das instruções de uso. Explique o que cada integração acrescenta e deixe claro o que continua funcionando sem ela.

Referência: [orientação do GitHub para arquivos README](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes).

## Changelog

O `CHANGELOG.md` segue o formato do [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e usa [Versionamento Semântico](https://semver.org/lang/pt-BR/).

- Mantenha `[Unreleased]` no topo.
- Liste as versões da mais nova para a mais antiga.
- Use datas no formato `AAAA-MM-DD`.
- Registre mudanças úteis para quem usa ou mantém o projeto.
- Não transforme o histórico de commits em changelog.
- Agrupe os itens em `Adicionado`, `Alterado`, `Removido`, `Corrigido` e `Segurança`. Use somente os grupos que tiverem conteúdo.
- Use `Em desuso` quando algo continuar disponível, mas tiver remoção planejada.
- Escreva cada item como uma frase curta e concreta.

## Organização das pastas

Arquivos esperados por ferramentas ou usados para iniciar o projeto podem ficar na raiz. Os demais seguem esta divisão:

| Conteúdo | Local |
|---|---|
| Visão geral | `README.md` |
| Histórico de versões | `CHANGELOG.md` |
| Arquitetura, planos, guias e pendências | `docs/` |
| Scripts de manutenção e migração | `scripts/` |
| Código do pacote | pasta do próprio pacote |
| Dados locais | `data/` |
| Arquivos temporários e resultados gerados | `.tmp/`, `cache/` ou `output/` |

Pastas de terceiros e arquivos gerados não devem ser reescritos para seguir este guia. A documentação própria deve apontar com clareza quando uma parte do projeto vem de outra fonte.
